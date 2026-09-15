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
"""

import json
import sys
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


# --- Summary ---

print(f"\n{'='*40}")
print(f"RESULT: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
