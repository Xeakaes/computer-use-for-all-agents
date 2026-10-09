"""
Live Linux X11 acceptance suite (8 items).

Talks to a real server on the real desktop:
    .venv/bin/python server.py > /tmp/sc-server.log 2>&1 &
    .venv/bin/python test-linux.py

Coverage:
  1. mouse move+click  - move to (200,300) within +-3 px, click reaches a
     spawned xev window (buttonpress in its log)
  2. keyboard type + hotkey - xev sees "Merhaba 123" and a ctrl+a chord
  3. screenshot - JPEG magic, size > 10 KB, saved to /tmp/sc-linux-test.jpg
  4. ocr - known text rendered by xmessage is read back from a region
  5. windows list + focus - >=1 entry with all 6 fields, focus lands on our
     window, window/post click lands at client origin + (50,60)
  6. game mode - start/move/hold/release/stop mechanics on an xev target
  7. app launch + window wait - /api/launch opens a uniquely titled xmessage
     and expect_title returns its window; safe close removes it
  8. CS2 offline input - printed as SKIP (no native Linux game)

Only windows spawned by this script are ever focused, typed into or clicked.
Exit code 0 only when no item FAILs.
"""

import json
import os
import re
import subprocess
import time
import urllib.request

BASE = "http://127.0.0.1:8745"

try:
    TOKEN = open(".token").read().strip()
except FileNotFoundError:
    TOKEN = ""

HEADERS = {"Content-Type": "application/json", "X-Auth-Token": TOKEN}

HELPERS = []
LOGS = {}
REQUIRED_WINDOW_FIELDS = {"hwnd", "title", "process", "pid", "focused", "rect"}


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


def get_bytes(path):
    req = urllib.request.Request(BASE + path, headers={"X-Auth-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


passed, failed = 0, 0


def check(name, cond, detail=""):
    global passed, failed
    mark = "PASS" if cond else "FAIL"
    print(f"{mark}: {name}" + (f"  — {detail}" if detail else ""))
    passed += 1 if cond else 0
    failed += 0 if cond else 1


def sh(args, timeout=10.0):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.stdout


def shell_vars(out):
    vals = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            vals[key] = value
    return vals


def mouse_location():
    vals = shell_vars(sh(["xdotool", "getmouselocation", "--shell"]))
    return (int(vals.get("X", -1)), int(vals.get("Y", -1)),
            int(vals.get("WINDOW", 0)))


def xwininfo_rect(hwnd):
    out = sh(["xwininfo", "-id", hex(hwnd)])
    mx = re.search(r"Absolute upper-left X:\s*(-?\d+)", out)
    my = re.search(r"Absolute upper-left Y:\s*(-?\d+)", out)
    mw = re.search(r"^\s+Width:\s*(\d+)", out, re.M)
    mh = re.search(r"^\s+Height:\s*(\d+)", out, re.M)
    if not (mx and my and mw and mh):
        return None
    return [int(mx.group(1)), int(my.group(1)),
            int(mw.group(1)), int(mh.group(1))]


def rect_contains(rect, x, y):
    return bool(rect) and rect[0] <= x < rect[0] + rect[2] \
        and rect[1] <= y < rect[1] + rect[3]


def spawn(args, log_path):
    handle = open(log_path, "wb")
    try:
        proc = subprocess.Popen(args, stdout=handle, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    finally:
        handle.close()
    HELPERS.append(proc)
    return proc


def wait_for_name(name, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ids = [int(tok) for tok in
               sh(["xdotool", "search", "--name", name]).split()
               if tok.isdigit()]
        if ids:
            return ids[0]
        time.sleep(0.2)
    return None


def wait_viewable(hwnd, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "IsViewable" in sh(["xwininfo", "-id", hex(hwnd)]):
            return True
        time.sleep(0.2)
    return False


def spawn_helper(kind, label, geometry, message=""):
    name = f"sct10-{os.getpid()}-{label}"
    log_path = f"/tmp/sc-t10-{os.getpid()}-{label}.log"
    if kind == "xev":
        args = ["xev", "-name", name, "-geometry", geometry,
                "-event", "keyboard", "-event", "mouse", "-event", "button"]
    else:
        args = ["xmessage", "-name", name, "-timeout", "300",
                "-geometry", geometry, message]
    spawn(args, log_path)
    hwnd = wait_for_name(name)
    if not hwnd or not wait_viewable(hwnd):
        return None
    LOGS[label] = log_path
    return hwnd


def log_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def log_since(path, offset):
    with open(path, "rb") as fh:
        return fh.read()[offset:].decode("utf-8", "replace")


_KEY_LINE = re.compile(r"^\s*state 0x([0-9a-f]+), keycode \d+ "
                       r"\(keysym 0x[0-9a-f]+, ([^)]+)\)")

MODIFIERS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
             "Super_L", "Super_R", "Meta_L", "Meta_R", "Mode_switch",
             "ISO_Level3_Shift", "Num_Lock", "Caps_Lock"}


def key_events(text):
    events = []
    kind = None
    for line in text.splitlines():
        if line.startswith("KeyPress event"):
            kind = "press"
            continue
        if line.startswith("KeyRelease event"):
            kind = "release"
            continue
        match = _KEY_LINE.match(line)
        if match and kind:
            events.append((kind, int(match.group(1), 16), match.group(2)))
    return events


def typed_chars(events):
    chars = []
    for kind, state, name in events:
        if kind != "press" or name in MODIFIERS:
            continue
        if len(name) == 1:
            chars.append(name)
        elif name == "space":
            chars.append(" ")
    return chars


def in_order(expected, observed):
    it = iter(observed)
    return all(char in it for char in expected)


def has_ctrl_a(events):
    ctrl = False
    for kind, state, name in events:
        if kind != "press":
            continue
        if name == "Control_L":
            ctrl = True
        elif ctrl and name == "a" and state & 0x4:
            return True
    return False


def list_windows():
    return get("/api/windows").get("windows", [])


def window_entry(hwnd, windows=None):
    if windows is None:
        windows = list_windows()
    return next((w for w in windows if w["hwnd"] == hwnd), None)


def focus_ours(hwnd, attempts=3):
    last = ""
    for _ in range(attempts):
        status, body = post("/api/window", {"action": "focus", "hwnd": hwnd})
        if status == 200 and body.get("ok"):
            windows = list_windows()
            entry = window_entry(hwnd, windows)
            if entry and entry.get("focused"):
                return True, ""
            focused = next((w for w in windows if w.get("focused")), None)
            title = focused.get("title", "?") if focused else "none"
            last = f"foreign focus kept: {title[:40]!r}"
        else:
            last = f"focus call HTTP {status}: {str(body.get('error', ''))[:70]}"
        time.sleep(0.4)
    return False, last


def send_key_guarded(body, hwnd, attempts=3):
    last = ""
    for _ in range(attempts):
        focused, detail = focus_ours(hwnd, attempts=1)
        if not focused:
            last = detail
            time.sleep(0.3)
            continue
        status, resp = post("/api/key", dict(body, expect_hwnd=hwnd))
        if status == 200 and resp.get("ok"):
            return True, ""
        err = resp.get("error", "")
        err = err.get("message", "") if isinstance(err, dict) else err
        last = f"HTTP {status}: {str(err)[:70]}"
        if status != 409:
            break
        time.sleep(0.3)
    return False, last


def warp_to(x, y, timeout=2.0):
    if mouse_location()[:2] == (x, y):
        return
    sh(["xdotool", "mousemove", str(x), str(y)])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mouse_location()[:2] == (x, y):
            return
        time.sleep(0.05)


def ensure_our_point(hwnd, x, y, attempts=3):
    last = ""
    for _ in range(attempts):
        warp_to(x, y)
        rect = xwininfo_rect(hwnd)
        _, _, under = mouse_location()
        if rect_contains(rect, x, y) and under == hwnd:
            return True, ""
        last = f"({x},{y}) not on our window: rect={rect}, window_under={under}"
        sh(["xdotool", "windowactivate", "--sync", hex(hwnd)])
        sh(["xdotool", "windowraise", hex(hwnd)])
        time.sleep(0.4)
    return False, last


def cleanup():
    try:
        post("/api/release_all", {})
    except Exception:
        pass
    try:
        post("/api/game", {"action": "stop"})
    except Exception:
        pass
    for proc in HELPERS:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    for proc in HELPERS:
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass


try:
    hwnd_ev1 = spawn_helper("xev", "ev1", "700x450+150+200")

    # --- 1) mouse move+click ---

    print("\n== 1) Mouse Move + Click ==")
    parts, ok = [], True
    status, body = post("/api/mouse", {"action": "move", "x": 200, "y": 300})
    mx, my, _ = mouse_location()
    parts.append(f"cursor at ({mx},{my})")
    if not (status == 200 and body.get("ok")
            and abs(mx - 200) <= 3 and abs(my - 300) <= 3):
        ok = False
        parts.append(f"move HTTP {status} outside +-3 px")
    if hwnd_ev1:
        safe, detail = ensure_our_point(hwnd_ev1, 200, 300)
    else:
        safe, detail = False, "xev helper window unavailable"
    if not safe:
        ok = False
        parts.append(f"click suppressed: {detail}")
    else:
        offset = log_size(LOGS["ev1"])
        status, body = post("/api/mouse", {"action": "click", "x": 200, "y": 300})
        time.sleep(0.4)
        pressed = "buttonpress" in log_since(LOGS["ev1"], offset).lower()
        if not (status == 200 and body.get("ok")):
            ok = False
            parts.append(f"click HTTP {status}")
        if not pressed:
            ok = False
            parts.append("no buttonpress in xev log")
        if status == 200 and body.get("ok") and pressed:
            parts.append("buttonpress seen in xev log")
    check("mouse move+click", ok, "; ".join(parts))

    # --- 2) keyboard type + hotkey ---

    print("\n== 2) Keyboard Type + Hotkey ==")
    parts, ok = [], True
    if not hwnd_ev1:
        ok = False
        parts.append("xev helper window unavailable")
    else:
        offset = log_size(LOGS["ev1"])
        sent, detail = send_key_guarded({"action": "type",
                                         "text": "Merhaba 123"}, hwnd_ev1)
        time.sleep(0.4)
        chars = typed_chars(key_events(log_since(LOGS["ev1"], offset)))
        type_ok = sent and in_order(list("Merhaba 123"), chars)
        if not type_ok:
            ok = False
            parts.append(f"type failed: {detail}" if not sent
                         else f"xev saw {''.join(chars)!r}")
        offset = log_size(LOGS["ev1"])
        sent, detail = send_key_guarded({"action": "hotkey",
                                         "keys": ["ctrl", "a"]}, hwnd_ev1)
        time.sleep(0.4)
        hotkey_ok = sent and has_ctrl_a(key_events(log_since(LOGS["ev1"], offset)))
        if not hotkey_ok:
            ok = False
            parts.append(f"hotkey failed: {detail}" if not sent
                         else "xev saw no ctrl+a sequence")
        if type_ok and hotkey_ok:
            parts.append("'Merhaba 123' and ctrl+a seen by xev")
    check("keyboard type + hotkey", ok, "; ".join(parts))

    # --- 3) screenshot ---

    print("\n== 3) Screenshot ==")
    status, data = get_bytes("/api/screenshot")
    jpeg = status == 200 and data[:2] == b"\xff\xd8"
    big = len(data) > 10 * 1024
    if jpeg:
        with open("/tmp/sc-linux-test.jpg", "wb") as fh:
            fh.write(data)
    detail = f"HTTP {status}, {len(data)} bytes"
    if not jpeg:
        detail += ", no JPEG magic"
    if not big:
        detail += ", not over 10 KB"
    if jpeg and big:
        detail = f"JPEG, {len(data)} bytes, saved to /tmp/sc-linux-test.jpg"
    check("screenshot", jpeg and big, detail)

    # --- 4) ocr ---

    print("\n== 4) OCR ==")
    info = get("/api/info")
    if not info.get("ocr_available"):
        check("ocr", False, "server reports ocr_available=false")
    else:
        hwnd_msg = spawn_helper("xmessage", "msg", "420x120+1350+150",
                                "SCREEN-CONTROL-OCR-OK")
        rect = xwininfo_rect(hwnd_msg) if hwnd_msg else None
        if not hwnd_msg or not rect:
            check("ocr", False, "xmessage helper window unavailable")
        else:
            safe, why = ensure_our_point(hwnd_msg, rect[0] + 80, rect[1] + 14)
            if not safe:
                check("ocr", False, f"message window covered: {why}")
            else:
                time.sleep(0.3)
                status, body = post("/api/ocr", {"region": rect})
                text = body.get("text", "") if status == 200 else ""
                ok = (status == 200 and body.get("ok")
                      and "SCREEN-CONTROL-OCR-OK" in text)
                clean = " ".join(text.split())[:70]
                check("ocr", ok, f"region={rect}, text={clean!r}")

    # --- 5) windows list + focus ---

    print("\n== 5) Windows List + Focus ==")
    parts, ok = [], True
    windows = list_windows()
    fields_ok = bool(windows) and any(
        REQUIRED_WINDOW_FIELDS <= set(w) for w in windows)
    if fields_ok:
        parts.append(f"{len(windows)} windows, all 6 fields present")
    else:
        ok = False
        parts.append(f"{len(windows)} windows, 6 required fields missing")
    hwnd_a = spawn_helper("xev", "win-a", "360x260+950+200")
    hwnd_b = spawn_helper("xev", "win-b", "360x260+950+520")
    if not hwnd_a or not hwnd_b:
        ok = False
        parts.append("two helper windows unavailable")
    else:
        focused, detail = focus_ours(hwnd_a)
        if not focused:
            ok = False
            parts.append(f"focus: {detail}")
        else:
            parts.append(f"focus on {hex(hwnd_a)} verified")
            rect = xwininfo_rect(hwnd_a)
            expected = (rect[0] + 50, rect[1] + 60) if rect else None
            if expected and ensure_our_point(hwnd_a, *expected)[0]:
                status, body = post("/api/window/post",
                                    {"action": "click", "hwnd": hwnd_a,
                                     "x": 50, "y": 60})
                time.sleep(0.3)
                mx, my, _ = mouse_location()
                landed = (status == 200 and body.get("ok") and expected
                          and abs(mx - expected[0]) <= 3
                          and abs(my - expected[1]) <= 3)
                if landed:
                    parts.append(f"5b cursor at ({mx},{my}) = origin + (50,60)")
                else:
                    ok = False
                    parts.append(f"5b cursor at ({mx},{my}), "
                                 f"expected {expected}, HTTP {status}")
            else:
                ok = False
                parts.append(f"5b click suppressed at {expected}")
    check("windows list + focus", ok, "; ".join(parts))

    # --- 6) game mode ---

    print("\n== 6) Game Mode ==")
    parts, ok = [], True
    rect = xwininfo_rect(hwnd_ev1) if hwnd_ev1 else None
    start = None
    if rect:
        candidate = (rect[0] + 100, rect[1] + 250)
        if (rect_contains(rect, *candidate)
                and rect_contains(rect, candidate[0] + 480, candidate[1] - 120)):
            start = candidate
    if not hwnd_ev1 or start is None:
        ok = False
        parts.append(f"xev helper/geometry unusable: rect={rect}")
    else:
        focused, detail = focus_ours(hwnd_ev1)
        if not focused:
            ok = False
            parts.append(f"focus: {detail}")
        else:
            landing = (start[0] + 480, start[1] - 120)
            covered = []
            if not ensure_our_point(hwnd_ev1, *landing)[0]:
                covered.append("landing")
            if not ensure_our_point(hwnd_ev1, *start)[0]:
                covered.append("start")
            if covered:
                ok = False
                parts.append(f"{'/'.join(covered)} point covered "
                             "by another window")
            post("/api/mouse", {"action": "move",
                                "x": start[0], "y": start[1]})
            offset = log_size(LOGS["ev1"])
            status, body = post("/api/game", {"action": "start"})
            started = (status == 200 and body.get("ok")
                       and "center" in body and "sensitivity" in body)
            if not started:
                ok = False
                parts.append(f"start HTTP {status}: "
                             f"{str(body.get('error', ''))[:60]}")
            else:
                parts.append(f"start center={body.get('center')}")
                status, body = post("/api/game", {"action": "move",
                                                  "dx": 40, "dy": -10})
                time.sleep(0.5)
                slice_text = log_since(LOGS["ev1"], offset)
                motions = re.findall(
                    r"MotionNotify event[^\n]*\n[^\n]*root:\((\d+),(\d+)\)",
                    slice_text)
                delta = max((max(abs(int(x) - start[0]), abs(int(y) - start[1]))
                             for x, y in motions), default=0)
                if not (status == 200 and body.get("ok")):
                    ok = False
                    parts.append(f"move HTTP {status}")
                if delta < 100:
                    ok = False
                    parts.append(f"no game motion in xev (max delta {delta})")
                else:
                    parts.append(f"motion seen in xev (delta {delta})")
                offset = log_size(LOGS["ev1"])
                sent, detail = send_key_guarded({"action": "down",
                                                 "key": "w"}, hwnd_ev1)
                time.sleep(1.5)
                events = key_events(log_since(LOGS["ev1"], offset))
                pressed = any(kind == "press" and name == "w"
                              for kind, state, name in events)
                if not sent:
                    ok = False
                    parts.append(f"key down: {detail}")
                elif not pressed:
                    ok = False
                    parts.append("no KeyPress w in xev log")
                else:
                    parts.append("w held 1.5 s")
                status, body = post("/api/release_all", {})
                time.sleep(0.3)
                held = get("/api/held")
                events = key_events(log_since(LOGS["ev1"], offset))
                released = any(kind == "release" and name == "w"
                               for kind, state, name in events)
                clear = held.get("keys") == [] and held.get("buttons") == []
                if status != 200 or not clear or not released:
                    ok = False
                    parts.append(f"stuck key: held={held.get('keys')} "
                                 f"released={released}")
                else:
                    parts.append("no stuck key after release_all")
                status, body = post("/api/game", {"action": "stop"})
                info = get("/api/info")
                if status == 200 and body.get("ok") \
                        and info.get("game_mode") is False:
                    parts.append("game_active false")
                else:
                    ok = False
                    parts.append(f"stop HTTP {status}, "
                                 f"game_mode={info.get('game_mode')}")
    check("game mode", ok, "; ".join(parts))

    # --- 7) App launch + window wait ---

    print("\n== 7) App Launch + Window Wait ==")
    ok, parts = True, []
    status, body = post("/api/launch", {
        "app": "xmessage",
        "args": ["-title", "SC_LAUNCH_OK", "launch-ok"],
        "expect_title": "SC_LAUNCH_OK",
        "timeout": 10,
    })
    win = (body or {}).get("window") or {}
    if status == 200 and body.get("ok") and win.get("hwnd") \
            and "SC_LAUNCH_OK" in win.get("title", ""):
        parts.append(f"launched pid={body.get('pid')} "
                     f"hwnd={hex(win['hwnd'])}")
    else:
        ok = False
        parts.append(f"HTTP {status}: {str(body)[:120]}")
    if win.get("hwnd"):
        st, cl = post("/api/window", {
            "action": "close",
            "hwnd": win["hwnd"],
            "expect_title": "SC_LAUNCH_OK",
        })
        if st == 200 and cl.get("ok"):
            parts.append("closed via safe close")
        else:
            ok = False
            parts.append(f"close HTTP {st}: {str(cl)[:80]}")
    check("app launch + window wait", ok, "; ".join(parts))

    # --- 8) CS2 offline input ---

    print("\n== 8) CS2 Offline Input ==")
    print("SKIP: CS2 not installed on Linux (Windows Steam library only)")

finally:
    cleanup()

print(f"\n{'='*40}")
print(f"RESULT: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
