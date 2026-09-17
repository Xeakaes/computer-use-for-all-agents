"""
screen-control web server.

Live screen feed + mouse/keyboard control API + window management + game mode.
Binds to localhost only by default (security).

Usage:
    python server.py                # http://127.0.0.1:8745
    python server.py --port 9000
    python server.py --log          # Enable structured logging

Authentication:
    - Session token: regenerated on every start, written to .token
    - Persistent API keys: stored in .apikeys (one key per line)
    Both are accepted via 'X-Auth-Token' header.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import secrets
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import importlib.util

from flask import Flask, Response, jsonify, request

from core.backends import get_backend
from core.errors import ApiError, focus_mismatch, invalid_target, window_not_found

_backend = get_backend()


def _b():
    """Current backend (module global so tests can swap it)."""
    return _backend


def _handle_error(exc):
    """Serialize an ApiError into the standardized error envelope."""
    return jsonify(exc.to_dict()), exc.status


def _input_blocked(exc):
    """Forbidden-input failures (PermissionError) as an INPUT_BLOCKED envelope."""
    return jsonify({"ok": False,
                    "error": {"code": "INPUT_BLOCKED", "message": str(exc),
                              "platform": None, "action": None,
                              "remediation": None}}), 403

app = Flask(__name__)

# Request-body ceiling: every JSON endpoint reads the full body, so an
# oversized payload (e.g. a multi-GB base64 image to /api/vision/diff) must
# be rejected before it is buffered. Flask answers 413 automatically.
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB

# ---------------------------------------------------------------------------
# Resource limits (SC-05) — every user-controllable size knob is capped so a
# misbehaving (or hostile) client cannot turn capture/OCR/input endpoints
# into memory or lock-starvation attacks.
# ---------------------------------------------------------------------------
MAX_REGION_DIM = 10_000            # max width/height of a capture region (px)
MAX_REGION_PIXELS = 33_177_600     # 7680x4320 — full 8K frame
SCALE_MIN, SCALE_MAX = 0.1, 2.0    # vision/frame & stream scale bounds
MAX_TEXT_CHARS = 10_000            # per /api/key & /api/window/post type call
MAX_STREAM_CLIENTS = 10            # concurrent MJPEG subscribers

# Pillow decompression-bomb guard for client-supplied images (SC-05):
# /api/vision/diff accepts a base64 previous frame; a crafted PNG can expand
# to gigabytes of RAM. 40 MP comfortably covers a full 8K frame.
try:
    from PIL import Image as _PILImage
    _PILImage.MAX_IMAGE_PIXELS = 40_000_000
except Exception:  # pragma: no cover - Pillow is a hard dependency anyway
    pass

# Host headers that may talk to the API when bound to loopback (SC-01).
# A DNS-rebinding page resolves its domain to 127.0.0.1 and its requests
# arrive with a foreign Host header — those are refused with 421.
_TRUSTED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _normalize_host(value: str) -> str:
    """Strip port (and IPv6 brackets) from a Host header value."""
    value = (value or "").strip().lower()
    if value.startswith("["):          # [::1]:8745
        return value.split("]", 1)[0].lstrip("[")
    if value.count(":") == 1:          # 127.0.0.1:8745
        return value.split(":", 1)[0]
    return value

# ---------------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

class StructuredFormatter(logging.Formatter):
    """JSON-formatted log entries for machine parsing."""
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
        }
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data
        return json.dumps(log_entry)

def setup_logging(enabled: bool = False):
    """Configure structured logging."""
    if not enabled:
        logging.disable(logging.CRITICAL)
        return
    # Re-enable logging: setup_logging(disabled) may have been called first
    # (module import), which globally disables ALL levels including ours.
    logging.disable(logging.NOTSET)
    handler = logging.FileHandler(LOG_DIR / "screen-control.jsonl")
    handler.setFormatter(StructuredFormatter())
    
    logger = logging.getLogger("screen-control")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger

logger = setup_logging()

# ---------------------------------------------------------------------------
# Session Replay — records all API calls for replay
# ---------------------------------------------------------------------------

_session_log = deque(maxlen=10000)  # Keep last 10k events
_session_start = time.time()

def log_event(event_type: str, data: dict = None):
    """Log an event for session replay."""
    event = {
        "t": time.time() - _session_start,  # Relative time
        "type": event_type,
    }
    if data:
        event["data"] = data
    _session_log.append(event)
    
    if logger:
        logger.info(event_type, extra={"extra_data": data})

# Two-stage locking: a long-running read (full-screen OCR ~3-5 s, MJPEG
# streaming) must not freeze input actions, and input must not block reads.
# Capture/OCR/vision and window enumeration are pure reads; mouse/keyboard/
# game/window mutations change OS state and are serialised separately.
_input_lock = threading.Lock()   # input actions (mouse, keyboard, game, window ops)
_read_lock = threading.Lock()    # capture, OCR, vision, monitor/window enumeration
_lock = _input_lock              # legacy alias — input side; do NOT use for reads
_stream_lock = threading.Lock()  # guards the MJPEG client counter
_stream_clients = 0              # active /api/stream subscribers (SC-05 cap)
_ocr_engine = None
_ocr_error = None
_bind_host = "127.0.0.1"  # Track bind address for security decisions

# ---------------------------------------------------------------------------
# Authentication — dual token system
#
# 1. Session token: regenerated on each server start (backward compatible)
# 2. Persistent API keys: stored in .apikeys file, survive restarts
#
# Both are accepted via X-Auth-Token header. Foreign origins cannot read
# either token due to Same-Origin Policy.
# ---------------------------------------------------------------------------

SESSION_TOKEN = secrets.token_hex(24)
SESSION_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".token")
APIKEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".apikeys")

# Load persistent API keys
_persistent_keys: set[str] = set()
_api_key_names: dict[str, str] = {}  # key_hash -> name
_api_key_expiry: dict[str, float] = {}  # key_hash -> unix epoch (None = never)

def _load_api_keys():
    """Load API keys from .apikeys file (format: hash\\tname per line).
    
    NOTE: The file stores pre-computed SHA256 hashes, NOT raw keys.
    We do NOT hash again here — that would be double-hashing.
    """
    global _persistent_keys, _api_key_names
    if not os.path.exists(APIKEYS_FILE):
        return
    try:
        with open(APIKEYS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                key_hash = parts[0].strip()  # Already a hash from file
                name = parts[1].strip() if len(parts) > 1 else "unnamed"
                # Optional third column: expiry (ISO timestamp or unix epoch).
                expiry = None
                if len(parts) > 2 and parts[2].strip():
                    raw_exp = parts[2].strip()
                    try:
                        expiry = (datetime.fromisoformat(raw_exp).timestamp()
                                  if ":" in raw_exp or "-" in raw_exp
                                  else float(raw_exp))
                    except (ValueError, OSError):
                        expiry = None  # unreadable expiry -> treat as never
                # Validate it looks like a hex hash (64 chars for SHA256)
                if len(key_hash) == 64 and all(c in '0123456789abcdef' for c in key_hash):
                    _persistent_keys.add(key_hash)
                    _api_key_names[key_hash] = name
                    if expiry is not None:
                        _api_key_expiry[key_hash] = expiry
    except Exception:
        pass

def _save_api_keys():
    """Save API keys to .apikeys file."""
    with open(APIKEYS_FILE, "w", encoding="utf-8") as f:
        f.write("# Screen Control API Keys\n")
        f.write("# Format: key\\tname (one per line)\n")
        f.write(f"# Generated: {datetime.now().isoformat()}\n\n")
        for key_hash, name in _api_key_names.items():
            exp = _api_key_expiry.get(key_hash)
            f.write(f"{key_hash}\t{name}"
                    + (f"\t{datetime.fromtimestamp(exp).isoformat()}" if exp else "")
                    + "\n")

# Write session token
with open(SESSION_TOKEN_FILE, "w", encoding="utf-8") as _fh:
    _fh.write(SESSION_TOKEN)

# Load persistent keys
_load_api_keys()


def _is_valid_token(token: str) -> bool:
    """Check if token is valid (session token or persistent API key).

    Persistent keys honour their optional expiry: an expired key is rejected
    even though its hash is still listed (SC-06: time-scoped credentials).
    """
    if secrets.compare_digest(token.encode("utf-8", "ignore"), SESSION_TOKEN.encode()):
        return True
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    if key_hash not in _persistent_keys:
        return False
    exp = _api_key_expiry.get(key_hash)
    return exp is None or time.time() < exp


@app.before_request
def _require_auth():
    # SC-01: when bound to loopback, only loopback Host headers are accepted.
    # This must run BEFORE the public-path exemption so /token cannot be read
    # by a DNS-rebinding page (its Host header is the attacker's domain).
    if _bind_host == "127.0.0.1":
        host = _normalize_host(request.host)
        if host and host not in _TRUSTED_HOSTS:
            return jsonify({"ok": False,
                            "error": f"untrusted Host header: {request.host} "
                                     f"(possible DNS rebinding)"}), 421
    # Exempt public endpoints
    if request.path in ("/", "/token"):
        return None
    supplied = request.headers.get("X-Auth-Token", "")
    if not supplied or not _is_valid_token(supplied):
        return jsonify({"ok": False, "error": "unauthorized: X-Auth-Token required"}), 401
    if request.method == "POST" and not request.is_json:
        return jsonify({"ok": False, "error": "Content-Type: application/json required"}), 415
    return None


# ---------------------------------------------------------------------------
# Watchdog — safety net for stuck keys and game-mode cursor lock
#
# Dynamic timeout based on activity type:
#   - Game mode: 30 seconds (faster response needed)
#   - Normal input: 60 seconds
#   - Idle: no timeout
# ---------------------------------------------------------------------------

HOLD_TIMEOUT_GAME = 30.0   # seconds for game mode
HOLD_TIMEOUT_NORMAL = 60.0 # seconds for normal input
_wd_lock = threading.Lock()
_wd_state = {"last_activity": time.time(), "last_watchdog": None, "count": 0,
             "timeout_mode": "normal"}


@app.after_request
def _touch_activity(resp):
    # Only POST requests (input/game commands) feed the watchdog
    if request.method == "POST":
        with _wd_lock:
            _wd_state["last_activity"] = time.time()
            # Detect if this is a game-related request
            if request.path.startswith("/api/game"):
                _wd_state["timeout_mode"] = "game"
            else:
                _wd_state["timeout_mode"] = "normal"
    return resp


def _get_watchdog_timeout() -> float:
    """Get dynamic timeout based on current mode."""
    with _wd_lock:
        mode = _wd_state["timeout_mode"]
    return HOLD_TIMEOUT_GAME if mode == "game" else HOLD_TIMEOUT_NORMAL


def _watchdog_check() -> dict | None:
    """Single watchdog tick; returns a summary if triggered."""
    with _wd_lock:
        idle = time.time() - _wd_state["last_activity"]
        # Read the mode under the already-held lock: calling
        # _get_watchdog_timeout() here would re-acquire the non-reentrant
        # _wd_lock and deadlock this thread (and every waiting POST).
        timeout = (HOLD_TIMEOUT_GAME if _wd_state["timeout_mode"] == "game"
                   else HOLD_TIMEOUT_NORMAL)
    try:
        held = _b().held_state()
        game_on = _b().game_active()
    except Exception:
        return None
    if (held["keys"] or held["buttons"] or game_on) and idle > timeout:
        # Serialise with live input requests: releasing keys while an input
        # request is mid-flight would leave the tracked set torn.
        with _input_lock:
            released = _b().release_all()
            if game_on:
                _b().game_stop()
        with _wd_lock:
            _wd_state["count"] += 1
            _wd_state["last_watchdog"] = {"at": time.time(), "idle": round(idle, 1),
                                          "released": released["released"],
                                          "timeout": timeout}
        print(f"[watchdog] {idle:.0f}s idle (timeout: {timeout}s) -> released: "
              f"{released['released']}", flush=True)
        return {"idle": round(idle, 1), "released": released["released"], "timeout": timeout}
    return None


def _watchdog_loop():
    while True:
        time.sleep(2.0)
        _watchdog_check()


# Critical system processes that must NEVER be killed
DENY_KILL_PROCS = {
    "winlogon.exe", "csrss.exe", "smss.exe", "services.exe", "lsass.exe",
    "svchost.exe", "system", "registry", "dwm.exe",
}


def get_ocr_engine():
    """Load the OCR engine on first call (may take a few seconds)."""
    global _ocr_engine, _ocr_error
    if _ocr_engine is None and _ocr_error is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        except Exception as exc:
            _ocr_error = str(exc)
    return _ocr_engine


def _ocr_available() -> bool:
    """OCR availability without triggering the (slow, optional) model load."""
    return _ocr_engine is not None or (
        _ocr_error is None
        and importlib.util.find_spec("rapidocr_onnxruntime") is not None)


def _index_path() -> Path:
    """Absolute path to index.html, next to this file (CWD-independent)."""
    return Path(__file__).parent / "index.html"


@app.get("/")
def index():
    try:
        resp = Response(_index_path().read_text(encoding="utf-8"),
                        mimetype="text/html")
    except FileNotFoundError:
        resp = Response(
            "<h1>screen-control</h1><p>Web UI file (index.html) not found. "
            "The REST API is unaffected.</p>", mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/token")
def token_bootstrap():
    """
    Token bootstrap for the bundled web UI.
    
    Security: Only works when binding to localhost (127.0.0.1).
    When binding to 0.0.0.0, /token is disabled to prevent network access.
    Use persistent API keys (--api-key) for remote access instead.
    """
    if _bind_host != "127.0.0.1":
        return jsonify({
            "ok": False, 
            "error": "Token endpoint disabled for non-localhost binding. Use persistent API keys instead."
        }), 403
    resp = jsonify({"ok": True, "token": SESSION_TOKEN, "type": "session"})
    # Never cache a credential in any layer (browser, proxy) (SC-01).
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/session")
def session_replay():
    """
    Get session replay data.
    Returns all recorded API calls for debugging/replay.
    """
    since = request.args.get("since", type=float)
    events = list(_session_log)
    if since is not None:
        events = [e for e in events if e["t"] >= since]
    return jsonify({
        "ok": True,
        "events": events,
        "count": len(events),
        "session_duration": round(time.time() - _session_start, 1),
    })


@app.post("/api/session/clear")
def session_clear():
    """Clear session log."""
    _session_log.clear()
    return jsonify({"ok": True, "message": "Session log cleared"})


@app.post("/api/keys")
def api_keys_manage():
    """
    API Key management:
      POST /api/keys {"action":"create","name":"my-key"}  -> creates new key
      POST /api/keys {"action":"create","name":"k","expires_in_hours":24}
                                                          -> time-scoped key (SC-06)
      POST /api/keys {"action":"list"}                     -> lists key names
      POST /api/keys {"action":"revoke","name":"my-key"}   -> revokes key

    Time-scoped keys are meant for clients that cannot send custom headers
    (e.g. web connectors taking the key in the URL): scope the blast radius
    by giving them a short lifetime and revoking them when done.
    """
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action", "list")
    
    if action == "create":
        name = body.get("name", f"key-{len(_persistent_keys)+1}")
        hours = body.get("expires_in_hours")
        new_key = secrets.token_hex(24)
        key_hash = hashlib.sha256(new_key.encode()).hexdigest()
        _persistent_keys.add(key_hash)
        _api_key_names[key_hash] = name
        expiry_iso = None
        if hours is not None:
            try:
                hours = float(hours)
                if hours <= 0:
                    raise ValueError
            except ValueError:
                _persistent_keys.discard(key_hash)
                del _api_key_names[key_hash]
                return jsonify({"ok": False,
                                "error": "expires_in_hours must be a positive number"}), 400
            expiry = time.time() + hours * 3600
            _api_key_expiry[key_hash] = expiry
            expiry_iso = datetime.fromtimestamp(expiry).isoformat()
        _save_api_keys()
        msg = "Save this key - it won't be shown again"
        if expiry_iso:
            msg += f" - expires {expiry_iso}"
        return jsonify({"ok": True, "key": new_key, "name": name,
                        "expires_at": expiry_iso,
                        "message": msg})
    
    elif action == "list":
        now = time.time()
        keys = [{"name": name, "hash": h[:8],
                 "expires_at": (datetime.fromtimestamp(e).isoformat()
                                if (e := _api_key_expiry.get(h)) else None),
                 "expired": bool(_api_key_expiry.get(h)) and _api_key_expiry[h] <= now}
                for h, name in _api_key_names.items()]
        return jsonify({"ok": True, "keys": keys, "count": len(keys)})
    
    elif action == "revoke":
        name = body.get("name")
        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400
        to_remove = [h for h, n in _api_key_names.items() if n == name]
        if not to_remove:
            return jsonify({"ok": False, "error": "key not found"}), 404
        for h in to_remove:
            _persistent_keys.discard(h)
            del _api_key_names[h]
            _api_key_expiry.pop(h, None)
        _save_api_keys()
        return jsonify({"ok": True, "revoked": name})
    
    return jsonify({"ok": False, "error": "unknown action"}), 400


@app.get("/api/info")
def info():
    with _read_lock:
        w, h = _b().screen_size()
        monitors = _b().list_monitors()
    caps = _b().get_capabilities()
    return jsonify({
        "ok": True,
        "width": w,
        "height": h,
        "monitors": monitors,
        "monitor_count": len(monitors),
        "backend": caps.get("platform"),
        "platform": caps.get("platform"),
        "ocr_available": _ocr_available(),
        "failsafe": True,
        "game_mode": _b().game_active(),
    })


@app.get("/api/capabilities")
def capabilities():
    """Active backend + what it can do. Agents should call this first."""
    caps = dict(_b().get_capabilities())  # copy: never mutate the backend's dict
    backend_name = caps.pop("platform", None)
    caps.setdefault("platform", backend_name)  # keep platform inside capabilities
    return jsonify({"ok": True, "backend": backend_name,
                    "capabilities": caps})


@app.get("/api/monitors")
def monitors():
    """List all available monitors."""
    with _read_lock:
        monitors = _b().list_monitors()
    return jsonify({"ok": True, "monitors": monitors, "count": len(monitors)})


@app.get("/api/screenshot")
def screenshot():
    monitor = request.args.get("monitor", 1, type=int)
    try:
        region = _validated_region()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _read_lock:
        data = _b().screenshot_jpeg(monitor, region)
    return Response(data, mimetype="image/jpeg")


# ---------------------------------------------------------------------------
# Vision access — raw pixel path for image-capable models
#
#   GET /api/vision/frame   single frame (raw JPEG or base64)
#   GET /api/stream         MJPEG live stream (bounded FPS; <img src> compatible)
#   POST /api/vision/diff   inter-frame motion (text summary; no vision needed)
#
# Complementary to OCR (text recognition): OCR gives you text, vision gives
# you the visual scene.  In games, HUD/blocks/enemies can only be detected
# through the vision path.
# ---------------------------------------------------------------------------

_last_gray = {"img": None}


def _validated_region():
    """
    Parse and bound-check the ?region=x,y,w,h query parameter (SC-05).
    Returns (x, y, w, h), None when absent, or raises ValueError with a
    client-safe message on malformed/out-of-bounds input.
    """
    r = request.args.get("region")
    if not r:
        return None
    try:
        x, y, w, h = (int(v) for v in r.split(","))
    except ValueError:
        raise ValueError("region must be x,y,w,h integers")
    if not (0 < w <= MAX_REGION_DIM and 0 < h <= MAX_REGION_DIM):
        raise ValueError(f"region w/h must be within 1..{MAX_REGION_DIM}")
    if w * h > MAX_REGION_PIXELS:
        raise ValueError(f"region area exceeds {MAX_REGION_PIXELS} px")
    if not (-MAX_REGION_DIM <= x <= MAX_REGION_DIM and
            -MAX_REGION_DIM <= y <= MAX_REGION_DIM):
        raise ValueError(f"region x/y must be within +-{MAX_REGION_DIM}")
    return (x, y, w, h)


def _validated_scale() -> float:
    """Parse and bound-check the ?scale= parameter (SC-05)."""
    scale = request.args.get("scale", 1.0, type=float)
    if not (SCALE_MIN <= scale <= SCALE_MAX):
        raise ValueError(f"scale must be within {SCALE_MIN}..{SCALE_MAX}")
    return scale


def _validated_text(body: dict) -> str:
    """Extract and length-check the 'text' field of an input request (SC-05)."""
    text = body.get("text", "")
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text must be a string of at most {MAX_TEXT_CHARS} chars")
    return text


@app.get("/api/vision/frame")
def vision_frame():
    """
    Single frame, image-model friendly:
      ?scale=0.5        downscale (default 1.0)
      ?gray=1           greyscale
      ?quality=70       JPEG quality
      ?format=base64    JSON {"b64": ...} — for text-only transports
      ?region=x,y,w,h   sub-region
    """
    try:
        region = _validated_region()
        scale = _validated_scale()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    gray = request.args.get("gray", 0, type=int)
    monitor = request.args.get("monitor", 1, type=int)
    quality = min(95, max(20, request.args.get("quality", 80, type=int)))
    as_b64 = request.args.get("format") == "base64"
    with _read_lock:
        img = _b().screenshot_scaled(monitor, region, scale=scale, grayscale=bool(gray))
    import io as _io
    import base64 as _b64
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    if as_b64:
        return jsonify({"ok": True, "width": img.width, "height": img.height,
                        "b64": _b64.b64encode(buf.getvalue()).decode("ascii")})
    return Response(buf.getvalue(), mimetype="image/jpeg")


@app.get("/api/stream")
def stream():
    """MJPEG live stream: ?fps=10 (1..30) &quality=&scale=&region=."""
    global _stream_clients
    fps = min(30, max(1, request.args.get("fps", 10, type=int)))
    quality = min(95, max(20, request.args.get("quality", 70, type=int)))
    try:
        scale = _validated_scale()
        region = _validated_region()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with _stream_lock:
        if _stream_clients >= MAX_STREAM_CLIENTS:
            return jsonify({"ok": False,
                            "error": f"too many concurrent streams "
                                     f"(limit {MAX_STREAM_CLIENTS})"}), 429
        _stream_clients += 1
    interval = 1.0 / fps

    def gen():
        global _stream_clients
        try:
            while True:
                t0 = time.time()
                with _read_lock:
                    img = _b().screenshot_scaled(1, region, scale=scale)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + buf.getvalue() + b"\r\n")
                time.sleep(max(0.0, interval - (time.time() - t0)))
        finally:
            with _stream_lock:
                _stream_clients -= 1

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/vision/diff")
def vision_diff():
    """
    Text-based motion detection (no vision needed):
      body {}                  compare against the last stored frame
      body {"grab":"gray"}     store the current frame after comparison
      body {"b64_prev":"..."}  compare against the provided previous frame
      ?region=x,y,w,h          sub-region
    Returns: changed, changed_pct, tiles (8x6 grid: pct + center coords), bbox.
    """
    try:
        region = _validated_region()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    body = request.get_json(force=True, silent=True) or {}
    import io as _io
    import base64 as _b64
    with _read_lock:
        if body.get("b64_prev"):
            try:
                from PIL import Image as _Image
                prev = _Image.open(_io.BytesIO(_b64.b64decode(body["b64_prev"])))
                prev.load()  # enforce the Pillow decompression-bomb limit now
            except Exception as exc:
                return jsonify({"ok": False, "error": f"b64_prev decode failed: {exc}"}), 400
        else:
            prev = _last_gray["img"]
        cur = _b().screenshot(1, region).convert("L")
        if body.get("grab") == "gray" or prev is None:
            _last_gray["img"] = cur
    if prev is None:
        return jsonify({"ok": True, "changed": False,
                        "note": "first frame stored; next call will return diff"})
    return jsonify({"ok": True, **_b().frame_diff(prev, cur)})


@app.post("/api/mouse")
def mouse():
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action", "move")
    x, y = body.get("x"), body.get("y")
    log_event("mouse", {"action": action, "x": x, "y": y})
    try:
        with _input_lock:
            if action == "move":
                _b().mouse_move(int(x), int(y))
            elif action == "click":
                _b().mouse_click(
                    int(x) if x is not None else None,
                    int(y) if y is not None else None,
                    body.get("button", "left"),
                    int(body.get("clicks", 1)),
                )
            elif action == "scroll":
                _b().mouse_scroll(int(body.get("clicks", 0)),
                                     int(x) if x is not None else None,
                                     int(y) if y is not None else None)
            elif action == "drag":
                _b().mouse_drag(
                    int(body.get("x1", 0)), int(body.get("y1", 0)),
                    int(x), int(y),
                    float(body.get("duration", 0.3)),
                    body.get("button", "left"),
                )
            elif action == "down":
                _b().mouse_down(body.get("button", "left"))
            elif action == "up":
                _b().mouse_up(body.get("button", "left"))
            else:
                return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.post("/api/key")
def key():
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action")
    log_event("key", {"action": action, "key": body.get("key"), "keys": body.get("keys")})
    try:
        # Resource limit (SC-05): reject oversized text before taking the lock.
        if action == "type":
            _validated_text(body)
        with _lock:
            # SAFETY: if expect_hwnd is given, verify the foreground window
            # matches — refuse to type into the wrong window.
            expect_hwnd = body.get("expect_hwnd")
            if expect_hwnd is not None and action in ("press", "hotkey", "type", "down"):
                fg = (_b().get_focused_window() or {}).get("hwnd")
                if fg != int(expect_hwnd):
                    fg_win = next((w for w in _b().list_windows()
                                   if w["hwnd"] == fg), None)
                    fg_name = fg_win["process"] if fg_win else "?"
                    return jsonify({"ok": False,
                                    "error": f"Focus mismatch: foreground is "
                                             f"{fg_name} — typing to the wrong "
                                             f"window has been blocked"}), 409
            if action == "press":
                _b().key_press(body["key"])
            elif action == "hotkey":
                _b().key_hotkey(*body.get("keys", []))
            elif action == "type":
                _b().type_text(_validated_text(body), float(body.get("interval", 0.03)))
            elif action == "down":
                _b().key_down(body["key"])
            elif action == "up":
                _b().key_up(body["key"])
            else:
                return _handle_error(ApiError("INVALID_TARGET",
                                              f"unknown action: {action}",
                                              status=400))
    except ApiError as exc:
        return _handle_error(exc)
    except PermissionError as exc:
        return _input_blocked(exc)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.get("/api/held")
def held():
    """Currently held keys/buttons + watchdog status."""
    st = _b().held_state()
    st["game_mode"] = _b().game_active()
    with _wd_lock:
        st["idle_seconds"] = round(time.time() - _wd_state["last_activity"], 1)
        st["watchdog_count"] = _wd_state["count"]
        st["last_watchdog"] = _wd_state["last_watchdog"]
    return jsonify({"ok": True, **st})


@app.post("/api/release_all")
def release_all_ep():
    """Emergency: release ALL held keys/buttons + lift game-mode cursor lock."""
    with _input_lock:
        res = _b().release_all()
        if _b().game_active():
            _b().game_stop()
            res["game_stopped"] = True
    return jsonify(res)


@app.post("/api/ocr")
def ocr():
    """OCR the screen (or a region) to extract text — the 'eyes' of the agent."""
    body = request.get_json(force=True, silent=True) or {}
    region = body.get("region")
    with _read_lock:
        img = _b().screenshot(region=tuple(region) if region else None)
    engine = get_ocr_engine()
    if engine is None:
        msg = ("OCR engine not installed. Install: python -m pip install rapidocr-onnxruntime"
               + (f" (error: {_ocr_error})" if _ocr_error else ""))
        return jsonify({"ok": False, "error": msg}), 503
    try:
        import numpy as np
        result, _ = engine(np.array(img))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    lines, items = [], []
    off_x = region[0] if region else 0
    off_y = region[1] if region else 0
    for line in result or []:
        box, text = line[0], line[1]
        cx = sum(p[0] for p in box) / 4 + off_x
        cy = sum(p[1] for p in box) / 4 + off_y
        lines.append(text)
        items.append({"text": text, "x": round(cx), "y": round(cy)})
    return jsonify({"ok": True, "text": "\n".join(lines), "lines": lines, "items": items})


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------


def _com_init():
    """Initialise COM for the current thread (needed by pyvda in Flask threads)."""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass


@app.get("/api/windows")
def windows():
    with _read_lock:
        wins = _b().list_windows()
    # Annotate which virtual desktop each window is on (silent if pyvda fails)
    try:
        _com_init()
        from pyvda import AppView
        for w in wins:
            try:
                w["desktop"] = AppView(hwnd=w["hwnd"]).desktop.number
            except Exception:
                w["desktop"] = None
    except Exception:
        pass
    return jsonify({"ok": True, "windows": wins})


@app.post("/api/window")
def window_op():
    """Window operations: focus | close | topmost | untopmost | kill | maximize"""
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action")
    hwnd = body.get("hwnd")
    try:
        with _input_lock:
            if action == "focus":
                _b().focus_window(int(hwnd))
            elif action == "maximize":
                _b().maximize_window(int(hwnd))
                time.sleep(0.3)
            elif action == "close":
                if hwnd is None:
                    return jsonify({"ok": False,
                                    "error": "hwnd required; no blind Alt+F4"}), 400
                # Optional focus guard: refuse unless the target really is the
                # foreground window (SC-04 parity, standardized envelope).
                if body.get("expect_focus"):
                    fg = (_b().get_focused_window() or {}).get("hwnd")
                    if fg != int(hwnd):
                        return _handle_error(focus_mismatch(f"hwnd={hwnd}",
                                                             f"hwnd={fg}"))
                res = _b().close_window(
                    int(hwnd),
                    expect_title=body.get("expect_title"),
                    expect_process=body.get("expect_process"))
                # A refused close (title/process mismatch) is a client error:
                # report it as 409 Conflict, not a silent 200. An unknown hwnd
                # gets the standardized WINDOW_NOT_FOUND envelope (404).
                if not res.get("ok"):
                    known = any(w["hwnd"] == int(hwnd)
                                for w in _b().list_windows())
                    if not known:
                        return _handle_error(window_not_found(f"hwnd={hwnd}"))
                    return jsonify(res), 409
                return jsonify(res)
            elif action in ("topmost", "untopmost"):
                _b().set_topmost(int(hwnd), action == "topmost")
            elif action == "kill":
                pid = int(body.get("pid", 0))
                if pid in (0, 4):
                    return jsonify({"ok": False, "error": "system process cannot be killed"}), 403
                if pid == os.getpid():
                    return jsonify({"ok": False, "error": "cannot kill own process"}), 403
                # SC-03: resolve the process name from the PID directly — not
                # from the visible-window inventory. A windowless (background/
                # headless) process previously bypassed the deny-list entirely.
                proc_name = _b().process_name(pid)
                if proc_name is None:
                    # Default-deny: a PID we cannot resolve may be a protected
                    # system process with a restricted query — refuse it.
                    return jsonify({"ok": False,
                                    "error": "cannot resolve process name; "
                                             "refusing to kill unknown pid"}), 403
                name = proc_name.lower()
                if name in DENY_KILL_PROCS:
                    return jsonify({"ok": False,
                                    "error": f"{name} is a critical system process"}), 403
                # Require confirmation of the expected process name to prevent
                # killing a newly-reused PID the client did not intend to hit.
                expect_proc = body.get("expect_process")
                if expect_proc and expect_proc.lower() != name:
                    return jsonify({"ok": False,
                                    "error": f"process mismatch: expected "
                                             f"'{expect_proc}', got '{name}'"}), 409
                return jsonify(_b().kill_process(pid))
            else:
                return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400
    except ApiError as exc:
        return _handle_error(exc)
    except PermissionError as exc:
        return _input_blocked(exc)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Focus-free (background) window control + virtual desktops
# ---------------------------------------------------------------------------


@app.get("/api/window/capture")
def window_capture():
    """
    Capture a window WITHOUT focusing it (PrintWindow).  Works even when the
    window is on another virtual desktop or behind other windows.
      ?hwnd=123         (required)
      ?client=1          client area only
      ?ocr=1             return OCR text + item coordinates instead of image
    """
    hwnd = request.args.get("hwnd", type=int)
    client = request.args.get("client", 0, type=int)
    want_ocr = request.args.get("ocr", 0, type=int)
    try:
        # Capture under the read lock; OCR inference runs OUTSIDE it so a slow
        # full-window OCR does not stall other readers (image-only requests).
        with _read_lock:
            img = _b().capture_window(hwnd, client_only=bool(client))
            if not want_ocr:
                import io as _io
                buf = _io.BytesIO()
                img.save(buf, "PNG")
                return Response(buf.getvalue(), mimetype="image/png")
        engine = get_ocr_engine()
        if engine is None:
            return jsonify({"ok": False, "error": "OCR engine not installed"}), 503
        import numpy as np
        result, _ = engine(np.array(img))
    except (ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    lines, items = [], []
    for line in result or []:
        box, text = line[0], line[1]
        cx = sum(p[0] for p in box) / 4
        cy = sum(p[1] for p in box) / 4
        lines.append(text)
        items.append({"text": text, "x": round(cx), "y": round(cy)})
    return jsonify({"ok": True, "text": "\n".join(lines), "items": items})


@app.post("/api/window/post")
def window_post():
    """
    Send input to a window, choosing the delivery path automatically.
      action: type | key | hotkey | click | scroll | drag
      mode:   auto (default) | background | focused
      Examples:
        {"hwnd":123, "action":"type", "text":"hello"}
        {"hwnd":123, "action":"key", "key":"enter"}
        {"hwnd":123, "action":"hotkey", "keys":["ctrl","s"]}
        {"hwnd":123, "action":"click", "x":50, "y":30}
        {"hwnd":123, "action":"scroll", "clicks":-3}
    Routing (mode=auto, decided by the backend's probe_input_mode):
      - "postmessage" -> classic Win32 app: background PostMessage path
                         (window keeps its current z-order/focus).
      - "uia"         -> WinUI/UWP/XAML surface: a single DirectX canvas with no
                         Win32 child controls — posted WM_* messages are silently
                         swallowed. The window is focused and the action is
                         replayed through SendInput (mouse coords converted
                         client->screen). This is the documented fallback for
                         modern apps; direct UIA synthesis is out of scope.
      - "focused"     -> window already foreground: focused SendInput path.
      - "invalid"     -> 409; not a reachable top-level window.
    Note: a handful of apps reject synthetic input on every path.
    """
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action")
    hwnd = body.get("hwnd")
    mode = body.get("mode", "auto")
    try:
        hwnd = int(hwnd)
        probe = _b().probe_input_mode(hwnd)
        if probe == "invalid":
            return jsonify({"ok": False,
                            "error": "window not reachable (bad hwnd or no message queue)"}), 409
        if mode == "auto":
            mode = "focused" if probe in ("focused", "uia") else "background"
        if mode not in ("background", "focused"):
            return jsonify({"ok": False, "error": f"unknown mode: {mode}"}), 400

        # Safety parity across ALL delivery paths (SC-02): background
        # PostMessage input must respect the same forbidden-key policy as the
        # direct /api/key route. Enforced here in addition to the control-layer
        # guard inside window_key()/window_hotkey() (defense in depth).
        if action == "key" and body.get("key"):
            _b().assert_allowed([str(body["key"])])
        if action == "hotkey" and body.get("keys"):
            _b().assert_allowed([str(k) for k in body["keys"]])

        # Resource limit (SC-05): reject oversized text before taking the lock.
        if action == "type":
            _validated_text(body)

        if mode == "focused":
            with _input_lock:
                result = _focused_window_input(hwnd, action, body)
            return jsonify(result)

        with _input_lock:
            if action == "type":
                return jsonify(_b().window_type_text(hwnd, _validated_text(body)))
            if action == "key":
                return jsonify(_b().window_key(hwnd, body["key"]))
            if action == "hotkey":
                return jsonify(_b().window_hotkey(hwnd, body.get("keys", [])))
            if action == "click":
                return jsonify(_b().window_click(
                    hwnd, int(body["x"]), int(body["y"]),
                    body.get("button", "left"), int(body.get("clicks", 1))))
            if action == "scroll":
                return jsonify(_b().window_scroll(hwnd, int(body.get("clicks", 0))))
            if action == "drag":
                return jsonify(_b().window_drag(
                    hwnd, int(body["x1"]), int(body["y1"]),
                    int(body["x"]), int(body["y"]), body.get("button", "left")))
            return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400
    except ApiError as exc:
        return _handle_error(exc)
    except PermissionError as exc:
        # Forbidden key combo on any delivery path (SC-02) -> INPUT_BLOCKED.
        return _input_blocked(exc)
    except RuntimeError as exc:
        # Focus verification failure (SC-04): the target window did not take
        # focus — a client--side conflict, not a server error.
        return jsonify({"ok": False, "error": str(exc)}), 409
    except (ValueError, KeyError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _focused_window_input(hwnd: int, action: str, body: dict) -> dict:
    """
    Focused path for /api/window/post: focus the window and replay the action
    through the real SendInput pipeline. Used for WinUI/UWP surfaces that
    swallow PostMessage (probe='uia') and when the caller forces mode='focused'.
    Mouse coordinates given in client space are converted with ClientToScreen
    (physical pixels — the process is Per-Monitor DPI aware).
    Raises ValueError/KeyError for unknown/missing parameters (-> HTTP 400).
    """
    _b().focus_window(hwnd)
    time.sleep(0.25)  # let the focus change settle before synthetic input
    # SC-04: verify the focus actually landed on the target BEFORE any
    # synthetic input. If Windows refused the focus switch or the user raced
    # the agent, SendInput would type into whatever IS foreground — the exact
    # scenario the safety model forbids. Refuse with 409 instead.
    fg = (_b().get_focused_window() or {}).get("hwnd")
    if fg != hwnd:
        fg_win = next((w for w in _b().list_windows()
                       if w["hwnd"] == fg), None)
        fg_name = fg_win["process"] if fg_win else "?"
        raise RuntimeError(
            f"focus verification failed: foreground is {fg_name}, not the "
            f"target window — input aborted to avoid typing into the wrong app")
    if action == "type":
        text = body.get("text", "")
        _b().type_text(text)
        return {"ok": True, "mode": "focused", "chars": len(text)}
    if action == "key":
        _b().key_press(body["key"])
        return {"ok": True, "mode": "focused", "key": body["key"]}
    if action == "hotkey":
        keys = body.get("keys", [])
        _b().key_hotkey(*keys)
        return {"ok": True, "mode": "focused", "keys": keys}
    if action == "click":
        sx, sy = _b().client_to_screen(hwnd, int(body["x"]), int(body["y"]))
        _b().mouse_click(sx, sy, body.get("button", "left"), int(body.get("clicks", 1)))
        return {"ok": True, "mode": "focused", "at": [sx, sy]}
    if action == "scroll":
        sx, sy = _b().client_to_screen(hwnd, int(body.get("x", 0)), int(body.get("y", 0)))
        _b().mouse_scroll(int(body.get("clicks", 0)), sx, sy)
        return {"ok": True, "mode": "focused", "clicks": int(body.get("clicks", 0))}
    if action == "drag":
        x1, y1 = _b().client_to_screen(hwnd, int(body["x1"]), int(body["y1"]))
        x2, y2 = _b().client_to_screen(hwnd, int(body["x"]), int(body["y"]))
        _b().mouse_drag(x1, y1, x2, y2, float(body.get("duration", 0.3)),
                           body.get("button", "left"))
        return {"ok": True, "mode": "focused", "from": [x1, y1], "to": [x2, y2]}
    raise ValueError(f"unknown action: {action}")


@app.get("/api/window/input-mode")
def window_input_mode():
    """
    Classify how a window best receives input before calling /api/window/post:
      focused | postmessage | uia | invalid
    """
    hwnd = request.args.get("hwnd", type=int)
    if hwnd is None:
        return jsonify({"ok": False, "error": "hwnd required"}), 400
    return jsonify({"ok": True, "hwnd": hwnd, "mode": _b().probe_input_mode(hwnd)})


@app.get("/api/window/children")
def window_children():
    """List child controls of a window (class name + title + hwnd)."""
    hwnd = request.args.get("hwnd", type=int)
    if hwnd is None:
        return jsonify({"ok": False, "error": "hwnd required"}), 400
    with _read_lock:
        return jsonify({"ok": True, "children": _b().list_children(hwnd)})


@app.get("/api/desktops")
def desktops():
    """List virtual desktops."""
    try:
        _com_init()
        from pyvda import VirtualDesktop, get_virtual_desktops
    except Exception as exc:
        return jsonify({"ok": False, "error": f"pyvda not available: {exc}"}), 503
    out = []
    with _read_lock:
        try:
            current_id = VirtualDesktop.current().id
        except Exception:
            current_id = None  # desktop enumeration still works without it
        for vd in get_virtual_desktops():
            out.append({"number": vd.number, "name": vd.name,
                        "current": current_id is not None and vd.id == current_id})
    return jsonify({"ok": True, "desktops": out})


@app.post("/api/desktop")
def desktop_op():
    """Desktop operations: switch (by number) | create. Never switches silently."""
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action")
    try:
        _com_init()
        from pyvda import VirtualDesktop, get_virtual_desktops
    except Exception as exc:
        return jsonify({"ok": False, "error": f"pyvda not available: {exc}"}), 503
    try:
        with _input_lock:
            if action == "switch":
                num = int(body["number"])
                targets = [d for d in get_virtual_desktops() if d.number == num]
                if not targets:
                    return jsonify({"ok": False, "error": f"desktop {num} not found"}), 404
                targets[0].go()
                return jsonify({"ok": True, "switched": num})
            if action == "create":
                vd = VirtualDesktop.create()
                return jsonify({"ok": True, "created": vd.number})
            return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Game mode
# ---------------------------------------------------------------------------


@app.post("/api/game")
def game():
    """Game mode operations: start | move | stop | heartbeat."""
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action")
    log_event("game", {"action": action, "sensitivity": body.get("sensitivity")})
    try:
        with _lock:
            if action == "start":
                return jsonify(_b().game_start(int(body.get("sensitivity", 12))))
            if action == "move":
                return jsonify(_b().game_move(
                    int(body.get("dx", 0)), int(body.get("dy", 0)),
                    int(body.get("sensitivity", 12))))
            if action == "stop":
                return jsonify(_b().game_stop())
            if action == "heartbeat":
                # Keep-alive signal for long holds (feeds the watchdog)
                return jsonify({"ok": True, "game_mode": _b().game_active()})
            return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400
    except ApiError as exc:
        return _handle_error(exc)
    except PermissionError as exc:
        return _input_blocked(exc)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def main():
    global logger, _bind_host
    parser = argparse.ArgumentParser(description="screen-control server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8745)
    parser.add_argument("--log", action="store_true",
                        help="Enable structured logging to logs/ directory")
    parser.add_argument("--api-key", 
                        help="Pre-set a persistent API key (for remote access)")
    args = parser.parse_args()
    
    _bind_host = args.host
    
    if args.log:
        logger = setup_logging(enabled=True)
        print(f"Structured logging enabled: {LOG_DIR / 'screen-control.jsonl'}")
    
    # Pre-register API key if provided
    if args.api_key:
        key_hash = hashlib.sha256(args.api_key.encode()).hexdigest()
        _persistent_keys.add(key_hash)
        _api_key_names[key_hash] = "cli-provided"
        _save_api_keys()
        print(f"API key registered: {args.api_key[:8]}...")
    
    if args.host != "127.0.0.1":
        print(f"⚠️  WARNING: binding to {args.host}")
        print(f"   /token endpoint is DISABLED for non-localhost binding.")
        print(f"   Use --api-key to set a persistent key for remote access.")
        print(f"   Anyone on the network can attempt to connect.")
    threading.Thread(target=_watchdog_loop, daemon=True).start()
    print(f"screen-control running: http://{args.host}:{args.port}")
    print(f"Auth: 'X-Auth-Token' header required on every request")
    if _bind_host == "127.0.0.1":
        print(f"Session token: {SESSION_TOKEN_FILE}")
    print(f"Watchdog: held input auto-released after {HOLD_TIMEOUT_GAME:.0f}s (game) / {HOLD_TIMEOUT_NORMAL:.0f}s (normal)")
    print("Stop: Ctrl+C  |  Emergency: move cursor to top-left corner "
          "(not in game mode: use physical Esc/Alt+Tab)")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
