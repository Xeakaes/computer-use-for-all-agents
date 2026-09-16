"""
Model Context Protocol (MCP) server for screen-control.

Exposes the screen-control HTTP API as MCP tools so any MCP-capable agent
(Claude Desktop, Claude Code, Cursor, VS Code Copilot Agent mode, custom
cloud agents, ...) can perceive and control this PC with a one-line
config — no custom glue code required.

Design:
  - Thin wrapper over the local REST API (http://127.0.0.1:8745). The Flask
    server remains the single source of truth: auth, locks, watchdog, focus
    guard and safety rules all stay there. This file adds no new powers,
    it only translates tools -> HTTP calls.
  - Requires the REST server to be running (start it first: `python server.py`
    or `start-server.bat`). Auto-start is deliberately NOT done here to avoid
    duplicate instances and duplicated auth tokens.
  - The auth token is read automatically from `.token` next to this file
    (written by server.py on first start), or from the SCREEN_CONTROL_TOKEN
    environment variable.
  - `screenshot` returns a real MCP image content block (JPEG), so
    image-capable models see the screen directly. Text-only models use
    `ocr_screen` / `motion_diff` instead.

Transports:
  stdio (default)   — for desktop agents (Claude Desktop, Cursor, ...):
                      "command": "python", "args": ["<path>/mcp_server.py"]
  streamable HTTP   — for remote/cloud agents:
                      python mcp_server.py --http --port 8751
                      Binds 127.0.0.1 only and requires the X-Auth-Token
                      header on every request (GET /health is open for
                      liveness probes). Expose via a tunnel for remote
                      access — start-server.bat automates this and prints
                      the ready-to-paste endpoint + token.

Usage (Claude Desktop / claude_desktop_config.json):
  {
    "mcpServers": {
      "screen-control": {
        "command": "python",
        "args": ["C:/path/to/screen-control/mcp_server.py"]
      }
    }
  }
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer, Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("SCREEN_CONTROL_URL", "http://127.0.0.1:8745")
_TOKEN_FILE = pathlib.Path(__file__).parent / ".token"
_APIKEYS_FILE = pathlib.Path(__file__).parent / ".apikeys"


def _token() -> str:
    """Auth token: env var first, then the .token file written by server.py."""
    tok = os.environ.get("SCREEN_CONTROL_TOKEN")
    if tok:
        return tok.strip()
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    raise RuntimeError(
        "No auth token: set SCREEN_CONTROL_TOKEN or make sure server.py has "
        "run once (it writes .token next to mcp_server.py)")


def _scoped_key_hashes() -> dict:
    """
    Load persistent API key hashes from .apikeys (hash -> expiry epoch|None).
    Used to validate scoped keys embedded in the URL path (/mcp/<key>) for
    headerless clients (SC-06). Cached by file mtime so revocations and
    creations by the REST server take effect immediately.
    """
    cache = _scoped_key_hashes._cache  # type: ignore[attr-defined]
    try:
        mtime = _APIKEYS_FILE.stat().st_mtime
    except OSError:
        return {}
    if cache["mtime"] != mtime:
        from datetime import datetime as _dt
        keys: dict = {}
        try:
            for line in _APIKEYS_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                h = parts[0].strip()
                exp = None
                if len(parts) > 2 and parts[2].strip():
                    raw = parts[2].strip()
                    try:
                        exp = (_dt.fromisoformat(raw).timestamp()
                               if (":" in raw or "-" in raw) else float(raw))
                    except (ValueError, OSError):
                        exp = None
                if len(h) == 64 and all(c in "0123456789abcdef" for c in h):
                    keys[h] = exp
        except OSError:
            keys = {}
        cache["mtime"] = mtime
        cache["keys"] = keys
    return cache["keys"]

_scoped_key_hashes._cache = {"mtime": None, "keys": {}}  # type: ignore[attr-defined]


def _api(method: str, path: str, body: dict | None = None,
         timeout: float = 30.0):
    """
    Call the REST API and always return a JSON-serializable dict.
    Non-2xx responses are returned as {"ok": false, "http_status": N, ...}
    instead of raising, so the agent sees the error text.
    """
    data = None
    headers = {"X-Auth-Token": _token()}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            payload = {}
        return {"ok": False, "http_status": exc.code, **payload}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _png_b64(path_query: str, timeout: float = 20.0) -> bytes:
    """Fetch a raw JPEG screenshot and return the bytes."""
    req = urllib.request.Request(BASE_URL + path_query,
                                 headers={"X-Auth-Token": _token()})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------

mcp = MCPServer(
    "screen-control",
    instructions=(
        "Perceive and control this Windows PC: read the screen (OCR for text, "
        "screenshot image for vision models, diff for motion), move/click the "
        "mouse, type on the keyboard, manage windows, and drive games via "
        "game mode (relative mouse). SAFETY: failsafe is on — physically move "
        "the cursor to the top-left corner to abort. Alt+F4 is blocked. "
        "Prefer expect_hwnd on typing to avoid wrong-window input."
    ),
)


# --- perception -------------------------------------------------------------

@mcp.tool()
def get_info() -> str:
    """Server/PC status: screen size, monitors, OCR availability, failsafe and game-mode state."""
    return json.dumps(_api("GET", "/api/info"))


@mcp.tool()
def ocr_screen(scan_focus: bool = False, region: str | None = None) -> str:
    """
    Read the screen as text with coordinates (OCR). Best for menus, dialogs,
    editors, chat — anything text-heavy. Returns lines plus per-item centers
    for clicking. 'region' is an optional "x,y,w,h" crop.
    """
    body: dict = {"scan_focus": scan_focus}
    if region:
        body["region"] = region
    return json.dumps(_api("POST", "/api/ocr", body))


@mcp.tool()
def screenshot(monitor: int = 1, region: str | None = None,
               scale: float = 1.0, quality: int = 80) -> Image:
    """
    Capture the screen as an image (for vision-capable models).
    'region' = "x,y,w,h"; 'scale' 0-1 shrinks to save bandwidth; 'quality' 1-100.
    """
    q = f"/api/vision/frame?monitor={monitor}&quality={quality}&scale={scale}"
    if region:
        q += f"&region={region}"
    raw = _png_b64(q)
    return Image(data=raw, format="jpeg")


@mcp.tool()
def motion_diff(region: str | None = None) -> str:
    """
    Text-only change detection between two snapshots: which tiles of the
    screen changed and their clickable centers. Use to verify an action had
    an effect or to spot movement without OCR/vision.
    """
    body: dict = {}
    if region:
        body["region"] = region
    return json.dumps(_api("POST", "/api/vision/diff", body))


# --- mouse / keyboard --------------------------------------------------------

@mcp.tool()
def mouse(action: str = "move", x: int | None = None, y: int | None = None,
          button: str = "left", clicks: int = 1, duration: float = 0.3,
          x1: int | None = None, y1: int | None = None) -> str:
    """
    Control the mouse. action: move | click | scroll | drag | down | up.
    click uses x,y (optional = current pos); drag goes (x1,y1)->(x,y);
    scroll uses 'clicks' (negative = down). Physical-pixel coordinates.
    """
    body: dict = {"action": action, "button": button, "clicks": clicks,
                  "duration": duration}
    if x is not None: body["x"] = x
    if y is not None: body["y"] = y
    if x1 is not None: body["x1"] = x1
    if y1 is not None: body["y1"] = y1
    return json.dumps(_api("POST", "/api/mouse", body))


@mcp.tool()
def keyboard(action: str = "press", key: str | None = None,
             keys: list[str] | None = None, text: str | None = None,
             interval: float = 0.03, expect_hwnd: int | None = None) -> str:
    """
    Control the keyboard. action: press | hotkey | type | down | up.
    press: key name (enter, f11, a...); hotkey: keys=["ctrl","s"];
    type: Unicode-safe text. expect_hwnd refuses (409) the action if the
    foreground window differs — use it to avoid typing into the wrong app.
    """
    body: dict = {"action": action, "interval": interval}
    if key is not None: body["key"] = key
    if keys: body["keys"] = keys
    if text is not None: body["text"] = text
    if expect_hwnd is not None: body["expect_hwnd"] = expect_hwnd
    return json.dumps(_api("POST", "/api/key", body))


@mcp.tool()
def get_held() -> str:
    """List currently held keys/buttons and watchdog status."""
    return json.dumps(_api("GET", "/api/held"))


@mcp.tool()
def release_all() -> str:
    """Release every held key and button (emergency reset). Safe to call anytime."""
    return json.dumps(_api("POST", "/api/release_all", {}))


# --- windows -----------------------------------------------------------------

@mcp.tool()
def window_children(hwnd: int) -> str:
    """
    List child controls of a window (class name, title, hwnd). Useful for
    classic Win32 apps with real child controls (e.g. an Edit box) — target
    the child hwnd in window_post. Modern WinUI/UWP apps have no classic
    children; use window_input_mode to detect them.
    """
    return json.dumps(_api("GET", f"/api/window/children?hwnd={hwnd}"))

@mcp.tool()
def list_windows() -> str:
    """List visible top-level windows: hwnd, title, process name, pid."""
    return json.dumps(_api("GET", "/api/windows"))


@mcp.tool()
def focus_window(hwnd: int) -> str:
    """Bring a window to the foreground (focus it)."""
    return json.dumps(_api("POST", "/api/window", {"action": "focus", "hwnd": hwnd}))


@mcp.tool()
def window_input_mode(hwnd: int) -> str:
    """
    Classify how a window receives input BEFORE posting to it:
    postmessage (classic Win32) | uia (WinUI/UWP: focus+SendInput fallback)
    | focused (already foreground) | invalid (dead window).
    """
    return json.dumps(_api("GET", f"/api/window/input-mode?hwnd={hwnd}"))


@mcp.tool()
def window_post(hwnd: int, action: str, text: str | None = None,
                key: str | None = None, keys: list[str] | None = None,
                x: int | None = None, y: int | None = None,
                mode: str = "auto") -> str:
    """
    Send input to a window in the background. action: type | key | hotkey |
    click | scroll. mode: auto (recommended — routes WinUI/UWP apps through
    the focused SendInput path automatically) | background | focused.
    Note: 'uia' routing focuses the window (unavoidable for modern apps).
    """
    body: dict = {"hwnd": hwnd, "action": action, "mode": mode}
    if text is not None: body["text"] = text
    if key is not None: body["key"] = key
    if keys: body["keys"] = keys
    if x is not None: body["x"] = x
    if y is not None: body["y"] = y
    return json.dumps(_api("POST", "/api/window/post", body))


@mcp.tool()
def window_capture_ocr(hwnd: int, ocr: bool = True) -> str:
    """
    Capture a background window WITHOUT focusing it; with ocr=1 (default)
    returns its text directly — read other apps' content while the user works.
    """
    return json.dumps(_api("GET", f"/api/window/capture?hwnd={hwnd}&ocr={1 if ocr else 0}"))


@mcp.tool()
def close_window(hwnd: int, expect_title: str | None = None,
                 expect_process: str | None = None) -> str:
    """
    Close a window safely (WM_CLOSE after verification — never blind Alt+F4).
    Pass expect_title/expect_process to refuse (409) if the window changed.
    """
    body: dict = {"action": "close", "hwnd": hwnd}
    if expect_title: body["expect_title"] = expect_title
    if expect_process: body["expect_process"] = expect_process
    return json.dumps(_api("POST", "/api/window", body))


# --- game mode ---------------------------------------------------------------

@mcp.tool()
def game(action: str, dx: int = 0, dy: int = 0,
         sensitivity: int = 12) -> str:
    """
    Game mode for FPS-style control. action: start (clip cursor to center)
    | move (relative camera look by dx,dy pixels * sensitivity) | stop
    | heartbeat (keep-alive during long holds). Combine with keyboard()
    down/up for WASD and mouse(action='down'/'up') for shooting/building.
    """
    body: dict = {"action": action, "sensitivity": sensitivity}
    if action == "move":
        body["dx"], body["dy"] = dx, dy
    return json.dumps(_api("POST", "/api/game", body))


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _run_stdio() -> None:
    mcp.run()  # stdio transport (default)


async def _send_json(send, status: int, payload: dict) -> None:
    """Minimal ASGI JSON responder (token guard replies / health probe)."""
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _run_http(port: int) -> None:
    """
    Streamable-HTTP transport for remote/cloud agents, wrapped in a token
    guard: every request must carry the X-Auth-Token header (same token as
    the REST server) — or, for clients that cannot send headers, embed a
    scoped API key in the URL path (/mcp/<key>; master token rejected there).
    GET /health is the only open path (liveness probe for start-server.bat).
    Binds 127.0.0.1 only — expose via a tunnel for remote access.
    """
    import uvicorn

    # NOTE: resolve the expected token per request, not once at startup.
    # server.py regenerates .token on every restart; when both servers are
    # (re)started together, a startup-time snapshot can capture the PREVIOUS
    # session token and every authenticated request would 401 afterwards.
    def _expected() -> str:
        return _token()
    # DNS-rebinding protection is disabled deliberately: tunneled/cloud
    # requests arrive with a foreign Host header, and the rebinding threat
    # (a malicious page making the visitor's browser talk to localhost)
    # is already covered by the token guard below — a rebinding page cannot
    # read the token, so every call it makes is rejected with 401.
    try:
        from mcp.server.transport_security import TransportSecuritySettings
        inner = mcp.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False))
    except ImportError:  # older SDK without TransportSecuritySettings
        inner = mcp.streamable_http_app()

    async def _guarded_app(scope, receive, send):
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path == "/health" and method in ("GET", "HEAD"):
            await _send_json(send, 200,
                             {"ok": True, "service": "screen-control-mcp"})
            return
        if method == "OPTIONS":  # CORS preflight carries no custom headers
            await inner(scope, receive, send)
            return

        # SC-06 compromise for headerless clients (e.g. web connectors):
        # /mcp/<scoped-key> authenticates a persistent, optionally time-scoped
        # API key embedded in the URL path. The master session token is
        # explicitly REJECTED in the URL — scope the blast radius instead:
        # short-lived key + revoke when done (POST /api/keys).
        import hashlib as _hashlib
        import re as _re
        import time as _time
        m = _re.fullmatch(r"/mcp/([0-9a-fA-F]{48})", path)
        if m:
            candidate = m.group(1).lower()
            if candidate == _expected():
                await _send_json(send, 403,
                                 {"ok": False,
                                  "error": "refusing the master session token "
                                           "in a URL; create a scoped key via "
                                           "POST /api/keys instead"})
                return
            kh = _hashlib.sha256(candidate.encode()).hexdigest()
            exp = _scoped_key_hashes().get(kh)
            if exp is None:
                await _send_json(send, 401,
                                 {"ok": False, "error": "unknown scoped key"})
                return
            if _time.time() >= exp:
                await _send_json(send, 403,
                                 {"ok": False, "error": "scoped key expired"})
                return
            scope = dict(scope)
            scope["path"] = "/mcp"
            await inner(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        # SC-06: header-only authentication. A ?token= query fallback was
        # removed deliberately — query strings persist in proxy/tunnel logs,
        # browser history and shared URLs, and this token grants full desktop
        # control. Clients that cannot send custom headers must use a wrapper
        # (e.g. a local stdio mcp_server.py proxying to this endpoint).
        supplied = headers.get("x-auth-token")
        if supplied != _expected():
            await _send_json(send, 401,
                             {"ok": False,
                              "error": "unauthorized: missing or invalid "
                                       "X-Auth-Token header"})
            return
        await inner(scope, receive, send)

    uvicorn.run(_guarded_app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="screen-control MCP server")
    parser.add_argument("--http", action="store_true",
                        help="Use streamable-HTTP transport instead of stdio")
    parser.add_argument("--port", type=int, default=8751,
                        help="HTTP port for --http mode (default: 8751)")
    args = parser.parse_args()
    if args.http:
        _run_http(args.port)
    else:
        _run_stdio()
