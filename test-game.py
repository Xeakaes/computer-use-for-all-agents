"""
Live game-mechanics test: hold-to-move + relative drag, then safe close.

Flow (mirrors a Minecraft session):
  1. Launch an app (mspaint; fall back to notepad)
  2. Focus + maximise the window
  3. Mouse DOWN -> relative movements (game_move) -> mouse UP  (block-breaking mechanic)
  4. Verify the drawing via pixel analysis
  5. Safe close: WM_CLOSE -> dialog found via OCR coords -> click "Don't Save"
  6. Verify the window actually closed
"""

import json
import subprocess
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
PASSED, FAILED = 0, 0


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers=HEADERS,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")[:200]}


def get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Auth-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def check(name, cond, detail=""):
    global PASSED, FAILED
    mark = "✓" if cond else "✗"
    print(f"{mark} {name}" + (f"  — {detail}" if detail else ""))
    PASSED += 1 if cond else 0
    FAILED += 0 if cond else 1


def find_window(process):
    for w in get("/api/windows")["windows"]:
        if w["process"].lower() == process.lower():
            return w
    return None


def ocr():
    return post("/api/ocr", {})[1]


# --- 1) Launch app ---

print("1) Launching application (mspaint, or notepad as fallback)")
proc = None
win = None
for exe, wait in (("mspaint.exe", 5), ("notepad.exe", 3)):
    try:
        p = subprocess.Popen([exe])
    except FileNotFoundError:
        print(f"   {exe} not found on this system, trying next...")
        continue
    for _ in range(10):
        time.sleep(1)
        win = find_window(exe)
        if win:
            break
    if win:
        proc = exe
        break
    print(f"   {exe} did not create a window, trying next...")

check("Window found", win is not None,
      f"{proc} pid={win['pid']} title={win['title'][:30]!r}" if win else "")
if win is None:
    print("   No suitable application found — aborting")
    raise SystemExit(1)


# --- 2) Focus + maximise ---

print("2) Focus + maximise")
post("/api/window", {"action": "focus", "hwnd": win["hwnd"]})
time.sleep(0.6)
post("/api/window", {"action": "maximize", "hwnd": win["hwnd"]})
time.sleep(0.8)
focused = [w for w in get("/api/windows")["windows"] if w["focused"]]
check("Focus on target window", focused and focused[0]["hwnd"] == win["hwnd"],
      focused[0]["title"][:40] if focused else "no focus")


# --- 3) Hold-to-draw (game mechanic) ---

if proc == "mspaint.exe":
    print("3) Hold + relative draw (block-breaking mechanic)")
    cx, cy = 660, 460  # safe interior of maximised Paint canvas
    post("/api/mouse", {"action": "move", "x": cx, "y": cy})
    time.sleep(0.4)
    post("/api/mouse", {"action": "down", "button": "left"})
    time.sleep(0.15)
    for dx, dy in ((50, 30), (50, 30), (50, 30), (50, 30),
                   (50, -30), (50, -30), (50, -30), (50, -30)):
        post("/api/game", {"action": "move", "dx": dx, "dy": dy, "sensitivity": 1})
        time.sleep(0.06)
    time.sleep(0.2)
    post("/api/mouse", {"action": "up", "button": "left"})
    time.sleep(0.6)

    import control
    img = control.screenshot(region=(80, 220, 900, 500))
    dark = sum(1 for px in img.getdata() if px[0] < 100 and px[1] < 100 and px[2] < 100)
    check("Drawing on canvas (dark pixels > 500)", dark > 500, f"{dark} dark pixels")
else:
    print("3) Typing text")
    post("/api/key", {"action": "type", "text": "safe close test"})
    time.sleep(1.0)
    t = ocr().get("text", "").lower()
    check("Text appears on screen", "safe close" in t or "safe close" in t.replace(" ", ""))


# --- 4) Safe close (WM_CLOSE) ---

print("4) Safe close: sending WM_CLOSE")
s, r = post("/api/window", {
    "action": "close",
    "hwnd": win["hwnd"],
    "expect_process": proc,
})
check("WM_CLOSE accepted", s == 200 and r.get("ok"), str(r)[:80])
time.sleep(1.5)


# --- 5) Handle "Don't Save" dialog ---

print("5) Dialog management (OCR coords)")
data = ocr()
items = data.get("items", [])
target = None
for it in items:
    tl = it["text"].lower()
    if proc == "notepad.exe" and ("don't save" in tl or "don t save" in tl):
        target = it
        break
    if proc == "mspaint.exe" and ("don't save" in tl or "discard" in tl):
        target = it
        break

if target:
    print(f"   Button found: {target['text']!r} @ ({target['x']},{target['y']})")
    post("/api/mouse", {"action": "click", "x": target["x"], "y": target["y"]})
else:
    # No dialog or OCR couldn't find it — use keyboard: right + Enter ("Don't Save")
    print("   OCR didn't find button — navigating with keyboard (right + enter)")
    post("/api/key", {"action": "press", "key": "right"})
    time.sleep(0.3)
    post("/api/key", {"action": "press", "key": "enter"})
time.sleep(2.0)


# --- 6) Verify closure ---

still = find_window(proc)
check("Window closed", still is None,
      f"still open: {still['title'][:30]}" if still else "")


# --- Summary ---

print(f"\n{'='*40}")
print(f"RESULT: {PASSED} passed, {FAILED} failed")
raise SystemExit(0 if FAILED == 0 else 1)
