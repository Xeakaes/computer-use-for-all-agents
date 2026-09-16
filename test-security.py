"""
Security & game-mode test suite (non-destructive).

Coverage:
  1. Deadly key combos (Alt+F4, Win, Delete) must be blocked at API level (403)
  2. Window list works, focused window is reported
  3. Safe close: wrong expect_title must abort the close (409/400)
  4. Critical system processes cannot be killed (403)
  5. Game mode: start -> cursor locked, move -> relative look, stop -> released
  6. Token auth: missing/wrong token -> 401, correct token -> 200
  7. POST Content-Type enforcement: non-JSON POST -> 415
  8. Host header trust (SC-01): foreign Host header -> 421 (DNS rebinding guard)
  9. Background-path safety parity (SC-02): Alt+F4 blocked via /api/window/post
 10. Resource limits (SC-05): oversized region/scale/text rejected with 4xx
 11. Scoped API keys (SC-06): authenticate, expire, revoke
 12. MCP scoped-key URL (optional): /mcp/<key> works, master token refused
"""

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8745"

# Read auth token
try:
    TOKEN = open(".token").read().strip()
except FileNotFoundError:
    TOKEN = ""

HEADERS = {"Content-Type": "application/json", "X-Auth-Token": TOKEN}


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers=HEADERS,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")[:200]}


def get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Auth-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get_raw(path, host=None):
    """GET with full error access (and optional Host header override)."""
    headers = {"X-Auth-Token": TOKEN}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")[:200]}


passed, failed = 0, 0


def check(name, cond, detail=""):
    global passed, failed
    mark = "✓" if cond else "✗"
    print(f"{mark} {name}" + (f"  — {detail}" if detail else ""))
    passed += 1 if cond else 0
    failed += 0 if cond else 1


# --- 1) Token auth ---

print("\n== Token Authentication ==")
# No token
req = urllib.request.Request(
    BASE + "/api/info",
    headers={"X-Auth-Token": ""},
)
try:
    urllib.request.urlopen(req, timeout=5)
    check("Missing token -> 401", False, "no error raised")
except urllib.error.HTTPError as e:
    check("Missing token -> 401", e.code == 401)

# Wrong token
req = urllib.request.Request(
    BASE + "/api/info",
    headers={"X-Auth-Token": "wrong_token_12345"},
)
try:
    urllib.request.urlopen(req, timeout=5)
    check("Wrong token -> 401", False, "no error raised")
except urllib.error.HTTPError as e:
    check("Wrong token -> 401", e.code == 401)

# Correct token (GET endpoint)
r = get("/api/info")
check("Correct token -> 200", r.get("ok"), str(r.get("width", "")))

# Non-JSON POST
req = urllib.request.Request(
    BASE + "/api/key",
    data=b"not json",
    headers={"Content-Type": "text/plain", "X-Auth-Token": TOKEN},
    method="POST",
)
try:
    urllib.request.urlopen(req, timeout=5)
    check("Non-JSON POST -> 415", False, "no error raised")
except urllib.error.HTTPError as e:
    check("Non-JSON POST -> 415", e.code == 415)


# --- 2) Blocked key combos ---

print("\n== Blocked Key Combos ==")
s, r = post("/api/key", {"action": "hotkey", "keys": ["alt", "f4"]})
check("Alt+F4 blocked (403)", s == 403, r.get("error", ""))
s, r = post("/api/key", {"action": "press", "key": "win"})
check("Win key blocked (403)", s == 403, r.get("error", ""))
s, r = post("/api/key", {"action": "hotkey", "keys": ["win", "d"]})
check("Win+D blocked (403)", s == 403, r.get("error", ""))
s, r = post("/api/key", {"action": "press", "key": "delete"})
check("Delete blocked (403)", s == 403, r.get("error", ""))


# --- 3) Window list ---

print("\n== Window List ==")
s, r = post("/api/windows", {})  # POST should be rejected (GET required)
check("Windows list requires GET", s == 405)

windows = get("/api/windows")["windows"]
check("Window list is non-empty", len(windows) > 0, f"{len(windows)} windows")
focused = [w for w in windows if w["focused"]]
check("Exactly one focused window", len(focused) == 1,
      focused[0]["title"][:40] if focused else "none")


# --- 4) Safe close: wrong title must abort ---

print("\n== Safe Close Verification ==")
if windows:
    w = next((x for x in windows if x["pid"] != 4), windows[0])
    s, r = post("/api/window", {
        "action": "close",
        "hwnd": w["hwnd"],
        "expect_title": "NONEXISTENT_TITLE_918273",
    })
    check("Wrong title aborts close", s in (400, 409) and not r.get("ok", False),
          r.get("error", "")[:60])


# --- 5) Critical process protection ---

print("\n== Critical Process Protection ==")
s, r = post("/api/window", {"action": "kill", "pid": 4})
check("System process (pid 4) rejected (403)", s == 403, r.get("error", ""))
s, r = post("/api/window", {"action": "kill", "pid": 0})
check("pid 0 rejected (403)", s == 403, r.get("error", ""))


# --- 6) Game mode ---

print("\n== Game Mode ==")
s, r = post("/api/game", {"action": "start", "sensitivity": 10})
check("Game mode started", s == 200 and r.get("ok"), str(r.get("center", "")))
s, r = post("/api/game", {"action": "move", "dx": 30, "dy": -15, "sensitivity": 10})
check("Relative camera look", s == 200 and r.get("ok"))
s, r = post("/api/game", {"action": "stop"})
check("Game mode stopped", s == 200 and r.get("ok"))


# --- 7) Watchdog dry run ---

print("\n== Watchdog (dry run) ==")
r = get("/api/held")
check("Held state returns ok", r.get("ok"))
check("Watchdog count reported", "watchdog_count" in r, str(r.get("watchdog_count", "")))


# --- 8) Host header trust (SC-01: DNS rebinding guard) ---

print("\n== Host Header Trust (SC-01) ==")
s, r = get_raw("/token", host="rebind.attacker.invalid:8745")
check("Foreign Host /token -> 421", s == 421, str(s))
s, r = get_raw("/token", host="127.0.0.1:8745")
check("Loopback Host /token -> 200", s == 200 and r.get("ok"), str(s))
s, r = get_raw("/api/info", host="rebind.attacker.invalid:8745")
check("Foreign Host API -> 421", s == 421, str(s))


# --- 9) Background-path safety parity (SC-02) ---

print("\n== Background Path Safety Parity (SC-02) ==")
if windows:
    target = windows[0]
    s, r = post("/api/window/post", {"hwnd": target["hwnd"], "action": "hotkey",
                                     "keys": ["alt", "f4"], "mode": "background"})
    check("Alt+F4 via window/post -> 403", s == 403, f"{s} {r.get('error', '')[:50]}")
    s, r = post("/api/window/post", {"hwnd": target["hwnd"], "action": "key",
                                     "key": "win", "mode": "background"})
    check("Win key via window/post -> 403", s == 403, f"{s} {r.get('error', '')[:50]}")


# --- 10) Resource limits (SC-05) ---

print("\n== Resource Limits (SC-05) ==")
s, r = post("/api/key", {"action": "type", "text": "x" * 20000})
check("Oversized text -> 400", s == 400, str(s))

req = urllib.request.Request(
    BASE + "/api/vision/frame?scale=50",
    headers={"X-Auth-Token": TOKEN})
try:
    urllib.request.urlopen(req, timeout=15)
    check("Huge scale -> 400", False, "no error raised")
except urllib.error.HTTPError as e:
    check("Huge scale -> 400", e.code == 400, str(e.code))

req = urllib.request.Request(
    BASE + "/api/screenshot?region=0,0,999999,999999",
    headers={"X-Auth-Token": TOKEN})
try:
    urllib.request.urlopen(req, timeout=15)
    check("Huge region -> 400", False, "no error raised")
except urllib.error.HTTPError as e:
    check("Huge region -> 400", e.code == 400, str(e.code))


# --- 11) Scoped API keys (SC-06): expiry + revocation ---

print("\n== Scoped API Keys (SC-06) ==")
s, r = post("/api/keys", {"action": "create", "name": "sec-test-key",
                          "expires_in_hours": 0.001})  # ~3.6 s
check("Scoped key created", s == 200 and r.get("ok") and r.get("key"),
      str(r.get("expires_at", "")))
sk = r.get("key", "")

req = urllib.request.Request(BASE + "/api/info", headers={"X-Auth-Token": sk})
try:
    urllib.request.urlopen(req, timeout=5)
    check("Scoped key authenticates", True)
except urllib.error.HTTPError as e:
    check("Scoped key authenticates", False, str(e.code))

r = post("/api/keys", {"action": "list"})[1]
entry = next((k for k in r.get("keys", []) if k["name"] == "sec-test-key"), None)
check("Expiry listed for key", entry is not None and entry.get("expires_at"))

time.sleep(4.5)  # let the ~3.6 s lifetime lapse
req = urllib.request.Request(BASE + "/api/info", headers={"X-Auth-Token": sk})
try:
    urllib.request.urlopen(req, timeout=5)
    check("Expired key rejected (401)", False, "no error raised")
except urllib.error.HTTPError as e:
    check("Expired key rejected (401)", e.code == 401, str(e.code))

s, r = post("/api/keys", {"action": "revoke", "name": "sec-test-key"})
check("Scoped key revoked", s == 200 and r.get("ok"))


# --- 12) MCP scoped-key URL (optional; needs the MCP HTTP server) ---

print("\n== MCP Scoped Key URL (SC-06, optional) ==")
try:
    urllib.request.urlopen("http://127.0.0.1:8751/health", timeout=2)
    mcp_up = True
except Exception:
    mcp_up = False
if mcp_up:
    s, r = post("/api/keys", {"action": "create", "name": "mcp-url-test",
                              "expires_in_hours": 1})
    mk = r.get("key", "")
    rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}}).encode()
    hdrs = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    req = urllib.request.Request(f"http://127.0.0.1:8751/mcp/{mk}", data=rpc,
                                 headers=hdrs)
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        check("Scoped key in MCP URL works", resp.status == 200, str(resp.status))
    except urllib.error.HTTPError as e:
        check("Scoped key in MCP URL works", False, str(e.code))
    req = urllib.request.Request(f"http://127.0.0.1:8751/mcp/{TOKEN}", data=rpc,
                                 headers=hdrs)
    try:
        urllib.request.urlopen(req, timeout=5)
        check("Master token in URL refused", False, "no error raised")
    except urllib.error.HTTPError as e:
        check("Master token in URL refused", e.code in (401, 403), str(e.code))
    post("/api/keys", {"action": "revoke", "name": "mcp-url-test"})
else:
    print("- MCP HTTP server (8751) not running; skipping optional checks")


# --- Summary ---

print(f"\n{'='*40}")
print(f"RESULT: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
