"""Windows implementation of the PlatformBackend interface — the reference backend.

Moved verbatim from control.py (now a compatibility shim). Win32 SendInput
path with pyautogui fallback, Per-Monitor DPI awareness (PMv2), Unicode
typing, background PostMessage input, game mode with ClipCursor.

SECURITY (unchanged):
- pyautogui FAILSAFE enabled — cursor to the top-left corner aborts everything.
- Window closing is NEVER a blind Alt+F4: WM_CLOSE only after verification.
- Deadly key combos are blocked via backends.forbidden (shared by all backends).
- Held-key tracking: every key_down / mouse_down is recorded; release_all()
  and the server watchdog release everything on demand.
"""

from __future__ import annotations

import io
import platform
import sys
import threading
import time

# Platform detection
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    # -----------------------------------------------------------------------
    # Per-Monitor DPI awareness (PMv2) — MUST be established BEFORE any
    # coordinate API runs in this process. Importing pyautogui (below) touches
    # coordinate APIs at import time, which would lock awareness to the
    # interpreter manifest's default (system-aware). So this block is
    # deliberately placed above that import.
    #
    # Without PMv2, on >100% display scaling Windows virtualises
    # GetSystemMetrics / GetWindowRect, so OCR/capture pixel coordinates and
    # pyautogui clicks drift apart. After this call every coordinate in this
    # module is a physical pixel, end to end (capture, OCR offsets, SendInput,
    # ClipCursor).
    #
    # Awareness can only be set ONCE per process and only before the first
    # DPI-dependent API call. Preference order:
    #   1. SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2) (Win10 1703+;
    #      also overrides a manifest that pinned system-aware)
    #   2. shcore.SetProcessDpiAwareness(2) (Win8.1+)
    #   3. user32.SetProcessDPIAware() (legacy, Vista+)
    # -----------------------------------------------------------------------
    import ctypes
    from ctypes import wintypes

    _DPI_AWARENESS_CTX_PMV2 = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    try:
        _SetCtx = ctypes.windll.user32.SetProcessDpiAwarenessContext
        _SetCtx.argtypes = [ctypes.c_void_p]
        _SetCtx.restype = wintypes.BOOL
        if not _SetCtx(_DPI_AWARENESS_CTX_PMV2):
            raise OSError("SetProcessDpiAwarenessContext failed")
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # legacy fallback (Vista+)
            except Exception:
                pass

import mss
import pyautogui
from PIL import Image

from core.backends import PlatformBackend

pyautogui.FAILSAFE = True   # cursor to top-left corner aborts all commands
pyautogui.PAUSE = 0.05      # short inter-command delay

# Public surface re-exported by control.py (compatibility shim) and used by
# server routes, tests, and the SDK.
__all__ = [
    "IS_WINDOWS", "FORBIDDEN_KEYS", "FORBIDDEN_HOTKEYS",
    "screen_size", "list_monitors", "get_platform", "screenshot",
    "screenshot_jpeg", "screenshot_scaled", "frame_diff",
    "mouse_move", "mouse_click", "mouse_scroll", "mouse_drag",
    "mouse_move_relative", "mouse_down", "mouse_up",
    "key_press", "key_down", "key_up", "key_hotkey", "type_text",
    "release_all", "held_state",
    "list_windows", "get_focused_window", "find_window", "focus_window",
    "_set_topmost", "close_window_safely", "kill_process",
    "capture_window", "vk_from_name", "process_name",
    "window_type_text", "window_key", "window_hotkey", "window_click",
    "window_scroll", "window_drag", "list_children", "pick_input_child",
    "probe_input_mode", "client_to_screen",
    "game_start", "game_move", "game_stop",
    "WindowsBackend",
]

# ---------------------------------------------------------------------------
# Win32 constants and structures (Windows only)
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD = 1
    INPUT_MOUSE = 0
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSE_MOVE_RELATIVE = 0
    SM_CXSCREEN = 0
    SM_CYSCREEN = 1
    HWND_TOPMOST = -1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    WM_CLOSE = 0x0010
    GWL_STYLE = -16
    WS_VISIBLE = 0x10000000
    WS_MINIMIZE = 0x20000000


    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]


    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]


    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]


    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        ]


    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", _INPUT_UNION),
        ]


    _user32 = ctypes.windll.user32

    # DPI awareness is established at module top, BEFORE the pyautogui import
    # (see the comment block there for why the placement is critical).

    _sendinput = _user32.SendInput
    _sendinput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _sendinput.restype = wintypes.UINT


    def _unicode_char(char: str, interval: float = 0.03) -> None:
        """Type a single character via Unicode (keyboard-layout independent)."""
        code = ord(char)
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.union.ki.wScan = code
            inp.union.ki.dwFlags = flags
            _sendinput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(interval / 2)
else:
    # macOS/Linux fallback: use pyautogui for Unicode typing
    def _unicode_char(char: str, interval: float = 0.03) -> None:
        """Type a single character via pyautogui."""
        pyautogui.press(char) if len(char) == 1 else pyautogui.write(char)
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------


def screen_size(monitor: int = 1) -> tuple[int, int]:
    """Return the logical screen size (pyautogui coordinate space)."""
    with mss.mss() as sct:
        mon = sct.monitors[monitor]
        return mon["width"], mon["height"]


def list_monitors() -> list[dict]:
    """List all available monitors with their dimensions."""
    monitors = []
    with mss.mss() as sct:
        for i, mon in enumerate(sct.monitors):
            if i == 0:  # Skip the "all-in-one" virtual monitor
                continue
            monitors.append({
                "id": i,
                "name": f"Monitor {i}",
                "left": mon["left"],
                "top": mon["top"],
                "width": mon["width"],
                "height": mon["height"],
                "is_primary": i == 1,
            })
    return monitors


def get_platform() -> str:
    """Return the current platform name."""
    return platform.system().lower()


def screenshot(monitor: int = 1, region: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Capture the monitor (or a sub-region) as a PIL Image."""
    with mss.mss() as sct:
        if region is None:
            mon = sct.monitors[monitor]
        else:
            x, y, w, h = region
            mon = {"left": x, "top": y, "width": w, "height": h}
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.rgb)


def screenshot_jpeg(monitor: int = 1, region: tuple[int, int, int, int] | None = None,
                    quality: int = 80) -> bytes:
    """Return JPEG bytes — faster than PNG for live streaming."""
    img = screenshot(monitor, region)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def screenshot_scaled(monitor: int = 1, region: tuple[int, int, int, int] | None = None,
                      scale: float = 1.0, grayscale: bool = False) -> Image.Image:
    """
    Scaled/grayscale frame for image-model consumption.
    scale < 1.0 shrinks the image (saves tokens/bandwidth for vision models).
    """
    img = screenshot(monitor, region)
    if scale != 1.0:
        resample = getattr(Image, "Resampling", Image).BILINEAR
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))), resample)
    if grayscale:
        img = img.convert("L")
    return img


# ---------------------------------------------------------------------------
# Frame-diff motion detection — text-based "what moved on screen"
#
# For agents that cannot see images (or want to save bandwidth):
#   8x6 tile grid, per-tile changed-pixel percentage + clickable tile-center
#   coordinates, plus an overall change bounding box.
#   All coordinates are relative to the captured region.
# ---------------------------------------------------------------------------

DIFF_GRID_COLS = 8
DIFF_GRID_ROWS = 6
DIFF_PIX_THRESHOLD = 12   # grayscale intensity difference threshold
DIFF_TILE_MIN_PCT = 1.0   # minimum % for a tile to count as "changed"


def frame_diff(prev: Image.Image, cur: Image.Image) -> dict:
    """Summarise the difference between two frames as structured text/data."""
    import numpy as np
    if prev.size != cur.size:
        cur = cur.resize(prev.size)
    a = np.asarray(prev.convert("L"), dtype=np.int16)
    b = np.asarray(cur.convert("L"), dtype=np.int16)
    d = np.abs(a - b)
    h, w = d.shape
    th, tw = max(1, h // DIFF_GRID_ROWS), max(1, w // DIFF_GRID_COLS)
    mask = d > DIFF_PIX_THRESHOLD
    tiles = []
    for r in range(DIFF_GRID_ROWS):
        for c in range(DIFF_GRID_COLS):
            t = mask[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
            pct = float(t.mean() * 100)
            if pct >= DIFF_TILE_MIN_PCT:
                tiles.append({"row": r, "col": c, "pct": round(pct, 1),
                              "center": [int((c + 0.5) * tw), int((r + 0.5) * th)]})
    bbox = None
    if mask.any():
        ys, xs = np.where(mask)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    return {"changed": bool(tiles), "bbox": bbox,
            "changed_pct": round(float(mask.mean() * 100), 2), "tiles": tiles}


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------


def mouse_move(x: int, y: int, duration: float = 0.15) -> None:
    """Move the cursor to absolute screen coordinates."""
    pyautogui.moveTo(x, y, duration=duration)


def mouse_click(x: int | None = None, y: int | None = None, button: str = "left",
                clicks: int = 1, interval: float = 0.1) -> None:
    """Click at the given position (absolute coordinates)."""
    pyautogui.click(x, y, button=button, clicks=clicks, interval=interval)


def mouse_scroll(clicks: int, x: int | None = None, y: int | None = None) -> None:
    """Scroll the mouse wheel (positive = up, negative = down)."""
    if x is not None and y is not None:
        pyautogui.moveTo(x, y)
    pyautogui.scroll(clicks)


def mouse_drag(x1: int, y1: int, x2: int, y2: int,
               duration: float = 0.3, button: str = "left") -> None:
    """Drag from (x1,y1) to (x2,y2) with the given button held."""
    pyautogui.moveTo(x1, y1)
    pyautogui.dragTo(x2, y2, duration=duration, button=button)


def mouse_move_relative(dx: int, dy: int) -> None:
    """Relative mouse movement — used for in-game camera look."""
    if IS_WINDOWS:
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi.dx = int(dx)
        inp.union.mi.dy = int(dy)
        inp.union.mi.dwFlags = MOUSEEVENTF_MOVE
        inp.union.mi.time = 0
        inp.union.mi.dwExtraInfo = 0
        _sendinput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    else:
        # macOS/Linux fallback: move relative to current position
        current_x, current_y = pyautogui.position()
        pyautogui.moveTo(current_x + dx, current_y + dy)


def mouse_down(button: str = "left") -> None:
    """Press and hold a mouse button; tracked for watchdog/release_all."""
    pyautogui.mouseDown(button=button)
    with _held_lock:
        _held_buttons.add(button)


def mouse_up(button: str = "left") -> None:
    """Release a held mouse button."""
    pyautogui.mouseUp(button=button)
    with _held_lock:
        _held_buttons.discard(button)


# ---------------------------------------------------------------------------
# Keyboard — held-key/button tracking ("sticky-key" protection)
#
# Every key_down / mouse_down is recorded; if the agent crashes,
# release_all() or the watchdog releases everything (the OS would otherwise
# keep the key held indefinitely).
# ---------------------------------------------------------------------------

_held_lock = threading.Lock()
_held_keys: set[str] = set()
_held_buttons: set[str] = set()

# Forbidden-key policy now lives in backends.forbidden (shared by all backends).
from backends.forbidden import (FORBIDDEN_KEYS, FORBIDDEN_HOTKEYS,   # noqa: F401
                                assert_forbidden as _assert_allowed)


def key_press(key: str) -> None:
    """Press and release a single key."""
    _assert_allowed([key])
    pyautogui.press(key)


def key_down(key: str) -> None:
    """Hold a key down (tracked for release_all / watchdog)."""
    _assert_allowed([key])
    pyautogui.keyDown(key)
    with _held_lock:
        _held_keys.add(key.lower())


def key_up(key: str) -> None:
    """Release a held key."""
    _assert_allowed([key])
    pyautogui.keyUp(key)
    with _held_lock:
        _held_keys.discard(key.lower())


def key_hotkey(*keys: str) -> None:
    """Press a key combination (e.g. key_hotkey('ctrl', 'c'))."""
    _assert_allowed(list(keys))
    pyautogui.hotkey(*keys)


def release_all() -> dict:
    """Emergency: release ALL held keys and mouse buttons."""
    with _held_lock:
        keys, buttons = list(_held_keys), list(_held_buttons)
        _held_keys.clear()
        _held_buttons.clear()
    released = []
    for k in keys:
        try:
            pyautogui.keyUp(k)
            released.append(k)
        except Exception as exc:
            released.append(f"{k} (release failed: {exc})")
    for b in buttons:
        try:
            pyautogui.mouseUp(button=b)
            released.append(f"mouse:{b}")
        except Exception as exc:
            released.append(f"mouse:{b} (release failed: {exc})")
    return {"ok": True, "released": released}


def held_state() -> dict:
    """Return currently held keys/buttons (for watchdog monitoring)."""
    with _held_lock:
        keys, buttons = sorted(_held_keys), sorted(_held_buttons)
    return {"keys": keys, "buttons": buttons}


def type_text(text: str, interval: float = 0.03) -> None:
    """Type a string via Unicode; Enter and Tab are sent as special keys."""
    for ch in text:
        if ch == "\n":
            pyautogui.press("enter")
        elif ch == "\t":
            pyautogui.press("tab")
        else:
            _unicode_char(ch, interval)


# ---------------------------------------------------------------------------
# Window management (safe closing)
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    def _get_window_title(hwnd: int) -> str:
        """Get the title text of a window by its handle."""
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value


    def list_windows() -> list[dict]:
        """List visible top-level windows: hwnd, title, process name, focus state."""
        EnumWindows = _user32.EnumWindows
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        IsWindowVisible = _user32.IsWindowVisible
        IsIconic = _user32.IsIconic
        GetWindowLongW = _user32.GetWindowLongW

        hwnds: list[int] = []

        @EnumProc
        def cb(hwnd, lparam):
            if IsWindowVisible(hwnd) and not IsIconic(hwnd):
                style = GetWindowLongW(hwnd, GWL_STYLE)
                if style & WS_VISIBLE:
                    hwnds.append(hwnd)
            return True

        EnumWindows(cb, 0)

        # Build pid -> process name map
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        pids = set()
        for hwnd in hwnds:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)

        pid_names: dict[int, str] = {}
        for pid in pids:
            h = kernel32.OpenProcess(0x0410, False, pid)  # PROCESS_QUERY_INFORMATION | QUERY_LIMITED
            if h:
                name = ctypes.create_unicode_buffer(260)
                if psapi.GetModuleBaseNameW(h, None, name, 260):
                    pid_names[pid] = name.value
                kernel32.CloseHandle(h)

        fg = _user32.GetForegroundWindow()
        result = []
        for hwnd in hwnds:
            title = _get_window_title(hwnd)
            if not title:
                continue
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            result.append({
                "hwnd": hwnd,
                "title": title,
                "process": pid_names.get(pid.value, "?"),
                "pid": pid.value,
                "focused": hwnd == fg,
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
            })
        return result


    def get_focused_window() -> dict | None:
        """Return the currently focused window, or None."""
        fg = _user32.GetForegroundWindow()
        for w in list_windows():
            if w["hwnd"] == fg:
                return w
        return None


    def find_window(title_contains: str | None = None, process: str | None = None,
                    hwnd: int | None = None) -> dict | None:
        """Find a window matching the given criteria (case-insensitive title match)."""
        for w in list_windows():
            if hwnd is not None:
                if w["hwnd"] == hwnd:
                    return w
                continue
            if process and w["process"].lower() != process.lower():
                continue
            if title_contains and title_contains.lower() not in w["title"].lower():
                continue
            return w
        return None


    def focus_window(hwnd: int) -> None:
        """
        Bring a window to the foreground and give it focus.

        Windows blocks background processes from stealing focus; this function
        works around that using the Alt-tap trick (sends a real Alt keypress so
        the system considers this process the "last input sender"), then falls
        back to AttachThreadInput, SwitchToThisWindow, and minimize+restore.
        """
        kernel32 = ctypes.windll.kernel32
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.1)

        # VK_MENU (0x12) is the Alt key — sending a real Alt tap makes this
        # process the "last input sender", which then allows
        # SetForegroundWindow. NOTE: 0x38 is the '8' key, NOT Alt.
        _alt = INPUT(type=INPUT_KEYBOARD)
        _alt.union.ki = KEYBDINPUT(0x12, 0, 0, 0, ctypes.c_size_t(0))          # VK_MENU down
        _alt_up = INPUT(type=INPUT_KEYBOARD)
        _alt_up.union.ki = KEYBDINPUT(0x12, 0, KEYEVENTF_KEYUP, 0, ctypes.c_size_t(0))
        arr = (INPUT * 2)(_alt, _alt_up)
        _user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        time.sleep(0.05)

        fg = _user32.GetForegroundWindow()
        fg_thread = _user32.GetWindowThreadProcessId(fg, None)
        target_thread = _user32.GetWindowThreadProcessId(hwnd, None)
        cur_thread = kernel32.GetCurrentThreadId()
        attached = fg_thread != target_thread
        if attached:
            _user32.AttachThreadInput(cur_thread, fg_thread, True)
            _user32.AttachThreadInput(cur_thread, target_thread, True)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        if attached:
            _user32.AttachThreadInput(cur_thread, fg_thread, False)
            _user32.AttachThreadInput(cur_thread, target_thread, False)
        time.sleep(0.25)

        if _user32.GetForegroundWindow() != hwnd:
            # Fallback 1: SwitchToThisWindow
            _switch = _user32.SwitchToThisWindow
            try:
                _switch(hwnd, True)
            except Exception:
                pass
            time.sleep(0.3)

        if _user32.GetForegroundWindow() != hwnd:
            # Fallback 2: minimize + restore (simulates taskbar click)
            _user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            time.sleep(0.15)
            _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.35)

        # SC-04: surface failure instead of silently returning. Callers that
        # replay SendInput right after this call MUST know the focus never
        # landed on the target — otherwise input goes to the foreground window
        # (the exact wrong-window scenario the safety model forbids).
        if _user32.GetForegroundWindow() != hwnd:
            raise RuntimeError(
                f"focus_window: could not bring hwnd {hwnd} to the foreground "
                f"(Windows refused the focus switch)")

    def _set_topmost(hwnd: int, topmost: bool = True) -> None:
        """Pin a window above all others (or unpin it)."""
        HWND_NOTOPMOST = -2
        insert_after = HWND_TOPMOST if topmost else HWND_NOTOPMOST
        _user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE)

    def close_window_safely(hwnd: int, expect_title: str | None = None,
                            expect_process: str | None = None) -> dict:
        """
        Close a window via WM_CLOSE after verifying it is the intended target.
        Never sends a blind Alt+F4: title/process must match the caller's
        expectation, otherwise the close is refused.
        """
        title = _get_window_title(hwnd)
        if not title:
            return {"ok": False, "error": "window has no title; refusing to close"}
        if expect_title and expect_title.lower() not in title.lower():
            return {"ok": False,
                    "error": f"title mismatch: expected '{expect_title}', got '{title}'"}
        if expect_process:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            h = kernel32.OpenProcess(0x0410, False, pid.value)
            name = ""
            if h:
                buf = ctypes.create_unicode_buffer(260)
                if psapi.GetModuleBaseNameW(h, None, buf, 260):
                    name = buf.value
                kernel32.CloseHandle(h)
            if name.lower() != expect_process.lower():
                return {"ok": False,
                        "error": f"process mismatch: expected '{expect_process}', got '{name or '?'}'"}
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(1.0)
        still_alive = bool(_user32.IsWindow(hwnd))
        return {"ok": True, "closed": not still_alive, "title": title,
                "note": "" if not still_alive else
                        "window still open (app may be showing a save prompt); "
                        "use kill with the pid if it must go"}

else:
    # macOS/Linux fallback: limited window management via pyautogui
    def list_windows() -> list[dict]:
        """List windows (limited on macOS/Linux - returns active window only)."""
        try:
            import subprocess
            if sys.platform == "darwin":  # macOS
                # Use osascript to get frontmost window
                result = subprocess.run(
                    ['osascript', '-e', 'tell application "System Events" to get name of first application process whose frontmost is true'],
                    capture_output=True, text=True, timeout=2
                )
                app_name = result.stdout.strip() if result.returncode == 0 else "Unknown"
                return [{
                    "hwnd": 1,  # Fake hwnd
                    "title": app_name,
                    "process": app_name.lower().replace(" ", "") + ".app",
                    "pid": 0,
                    "focused": True,
                    "rect": [0, 0, 1920, 1080],
                }]
            else:  # Linux
                # Use xdotool if available
                result = subprocess.run(
                    ['xdotool', 'getactivewindow', 'getwindowname'],
                    capture_output=True, text=True, timeout=2
                )
                title = result.stdout.strip() if result.returncode == 0 else "Unknown"
                return [{
                    "hwnd": 1,
                    "title": title,
                    "process": "unknown",
                    "pid": 0,
                    "focused": True,
                    "rect": [0, 0, 1920, 1080],
                }]
        except Exception:
            return [{"hwnd": 1, "title": "Active Window", "process": "unknown", 
                     "pid": 0, "focused": True, "rect": [0, 0, 1920, 1080]}]

    def get_focused_window() -> dict | None:
        """Return the currently focused window."""
        windows = list_windows()
        return windows[0] if windows else None

    def find_window(title_contains: str | None = None, process: str | None = None,
                    hwnd: int | None = None) -> dict | None:
        """Find a window matching the given criteria."""
        for w in list_windows():
            if hwnd is not None and w["hwnd"] == hwnd:
                return w
            if process and w["process"].lower() != process.lower():
                continue
            if title_contains and title_contains.lower() not in w["title"].lower():
                continue
            return w
        return None

    def focus_window(hwnd: int) -> None:
        """Bring window to focus (limited on macOS/Linux)."""
        # On macOS/Linux, pyautogui can't reliably focus windows
        # This is a no-op; the user must focus manually
        pass

    def _set_topmost(hwnd: int, topmost: bool = True) -> None:
        """Set or remove always-on-top (not supported on macOS/Linux)."""
        pass

    def close_window_safely(hwnd: int, expect_title: str | None = None,
                            expect_process: str | None = None) -> dict:
        """Close window safely (limited on macOS/Linux)."""
        return {"ok": False, "error": "Window closing not supported on this platform"}

    def kill_process(pid: int) -> dict:
        """Kill a process by PID."""
        import subprocess
        try:
            if sys.platform == "darwin":
                r = subprocess.run(["kill", str(pid)], capture_output=True, text=True)
            else:
                r = subprocess.run(["kill", str(pid)], capture_output=True, text=True)
            return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()}
        except Exception as e:
            return {"ok": False, "output": str(e)}


def kill_process(pid: int) -> dict:
    """Kill a process by PID (task-manager style, no window focusing needed)."""
    import subprocess
    if IS_WINDOWS:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
    else:
        r = subprocess.run(["kill", "-9", str(pid)], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()}


def _maximize_window(hwnd: int) -> None:
    """Maximize a window (moved from server.py's /api/window maximize action)."""
    if IS_WINDOWS:
        _user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE


# ---------------------------------------------------------------------------
# Game mode — camera look via relative mouse, hold-to-move keys
# ---------------------------------------------------------------------------

_first_mon = None


def _primary_metrics() -> tuple[int, int]:
    """Return the primary monitor resolution (cached)."""
    global _first_mon
    if _first_mon is None:
        if IS_WINDOWS:
            _first_mon = (
                _user32.GetSystemMetrics(SM_CXSCREEN),
                _user32.GetSystemMetrics(SM_CYSCREEN),
            )
        else:
            # Fallback: use pyautogui
            _first_mon = pyautogui.size()
    return _first_mon


def game_start(mouse_sensitivity: int = 12) -> dict:
    """
    Start game mode:
      - Cursor is locked to a 2×2 box at screen center (Windows)
      - Camera look uses relative mouse movement
    """
    release_all()  # clean start
    cx, cy = _primary_metrics()
    if IS_WINDOWS:
        box = wintypes.RECT(cx // 2 - 1, cy // 2 - 1, cx // 2 + 1, cy // 2 + 1)
        _user32.ClipCursor(ctypes.byref(box))
        _user32.SetCursorPos(cx // 2, cy // 2)
    else:
        # macOS/Linux: move cursor to center (no clip)
        pyautogui.moveTo(cx // 2, cy // 2)
    return {"ok": True, "center": [cx // 2, cy // 2], "sensitivity": mouse_sensitivity,
            "note": "cursor locked to center; use /api/game/move for camera look"}


def game_move(dx: int, dy: int, sensitivity: int = 12) -> dict:
    """Rotate the in-game camera via pure relative mouse movement."""
    mouse_move_relative(int(dx) * sensitivity, int(dy) * sensitivity)
    return {"ok": True}


def _game_active() -> bool:
    """Check whether game mode is currently active."""
    if IS_WINDOWS:
        box = wintypes.RECT()
        _user32.GetClipCursor(ctypes.byref(box))
        cx, cy = _primary_metrics()
        return (box.right - box.left) <= 4
    return False  # Not implemented on macOS/Linux


def game_stop() -> dict:
    """Stop game mode: release cursor and all held input."""
    if IS_WINDOWS:
        _user32.ClipCursor(None)
    res = release_all()
    return {"ok": True, "released": res["released"]}


# ---------------------------------------------------------------------------
# Focus-free (background) control — PrintWindow + PostMessage
#
# Purpose: capture and control a window WITHOUT bringing it to the foreground
# or stealing focus.  The user keeps working on their main desktop while the
# assistant reads/writes a background window (even on a different virtual
# desktop).
# ---------------------------------------------------------------------------

WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MOUSEWHEEL = 0x020A
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


def capture_window(hwnd: int, client_only: bool = False) -> Image.Image:
    """
    Capture a window via PrintWindow WITHOUT focusing it.
    Works even when the window is behind others, on another virtual desktop,
    or minimised.
    """
    gdi32 = ctypes.windll.gdi32
    rc = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        raise ValueError("Invalid window size")
    flags = (PW_CLIENTONLY | PW_RENDERFULLCONTENT) if client_only else PW_RENDERFULLCONTENT
    hdc_win = _user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(mem, bmp)
    ok = _user32.PrintWindow(hwnd, mem, flags)
    gdi32.SelectObject(mem, old)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # top-down scanline order
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0      # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)  # DIB_RGB_COLORS
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    _user32.ReleaseDC(hwnd, hdc_win)
    if not ok:
        raise RuntimeError("PrintWindow failed — this window cannot be captured")
    return Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")


def _scan_code(vk: int) -> int:
    """Map a virtual-key code to its hardware scan code."""
    return _user32.MapVirtualKeyW(vk, 0)


_VK_NAMES = {
    "enter": 0x0D, "return": 0x0D, "backspace": 0x08, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
}


def vk_from_name(name: str) -> int:
    """Resolve a human-readable key name to its Win32 virtual-key code."""
    n = name.strip().lower()
    if n in _VK_NAMES:
        return _VK_NAMES[n]
    if len(n) == 1 and (n.isalnum()):
        return ord(n.upper())
    if n.isdigit():
        return int(n)
    raise ValueError(f"Unknown key name: {name}")


def process_name(pid: int) -> str | None:
    """
    Resolve a PID to its process (executable base) name via the Win32 toolhelp
    snapshot (SC-03). Works for windowless/background processes; returns None
    when the PID does not exist or cannot be queried.
    """
    if not IS_WINDOWS:
        return None
    import ctypes
    TH32CS_SNAPPROCESS = 0x2
    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong),
                    ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1 or snap == 0xFFFFFFFFFFFFFFFF:  # INVALID_HANDLE_VALUE (-1)
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == pid:
                return entry.szExeFile
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return None  # PID not found in the snapshot
    finally:
        kernel32.CloseHandle(snap)


def window_type_text(hwnd: int, text: str) -> dict:
    """
    Type text into a window WITHOUT focusing it (WM_CHAR).
    Unicode-safe (Turkish characters included).
    Note: unlike focused typing via SendInput, this sends messages directly
    to the window; some apps may not accept synthetic input.
    """
    sent = 0
    for ch in text:
        if ch in "\n\r":
            vk, scan = 0x0D, _scan_code(0x0D)
            _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, (scan << 16) | 1)
            _user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000000 | (scan << 16) | 1)
        elif ch == "\t":
            vk, scan = 0x09, _scan_code(0x09)
            _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, (scan << 16) | 1)
            _user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000000 | (scan << 16) | 1)
        else:
            _user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
        sent += 1
        time.sleep(0.01)
    return {"ok": True, "chars": sent}


def window_key(hwnd: int, key: str) -> dict:
    """
    Send a single key press to a window WITHOUT focusing it (VK-based).
    Safety parity (SC-02): the forbidden-key policy applies to the background
    path too — an agent must not reach Alt+F4 etc. through PostMessage when
    the direct route blocks it.
    """
    _assert_allowed([key])
    vk = vk_from_name(key)
    scan = _scan_code(vk)
    _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, (scan << 16) | 1)
    time.sleep(0.02)
    _user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000000 | (scan << 16) | 1)
    return {"ok": True, "vk": vk}


def window_hotkey(hwnd: int, keys: list) -> dict:
    """
    Send a key combination to a window WITHOUT focusing it (e.g. ['ctrl','s']).
    Safety parity (SC-02): forbidden hotkeys are blocked on the background
    path exactly like on the focused SendInput path.
    """
    _assert_allowed(list(keys))
    vks = [vk_from_name(k) for k in keys]
    for vk in vks:
        _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, (_scan_code(vk) << 16) | 1)
        time.sleep(0.02)
    for vk in reversed(vks):
        _user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000000 | (_scan_code(vk) << 16) | 1)
        time.sleep(0.02)
    return {"ok": True, "vks": vks}


def window_click(hwnd: int, x: int, y: int, button: str = "left",
                 clicks: int = 1) -> dict:
    """
    Send a mouse click to a window WITHOUT focusing it (client coordinates).
    clicks=2 sends a double-click (WM_*BUTTONDBLCLK).
    Note: not all apps accept synthetic mouse messages.
    """
    lp = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
    if button == "left":
        mk, down, dbl, up = MK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONDBLCLK, WM_LBUTTONUP
    else:
        mk, down, dbl, up = MK_RBUTTON, WM_RBUTTONDOWN, WM_RBUTTONDBLCLK, WM_RBUTTONUP
    _user32.PostMessageW(hwnd, WM_MOUSEMOVE, mk, lp)
    if int(clicks) >= 2:
        _user32.PostMessageW(hwnd, dbl, mk, lp)
        _user32.PostMessageW(hwnd, up, 0, lp)
    else:
        _user32.PostMessageW(hwnd, down, mk, lp)
        _user32.PostMessageW(hwnd, up, 0, lp)
    return {"ok": True, "x": x, "y": y, "button": button, "clicks": int(clicks)}


def window_scroll(hwnd: int, clicks: int) -> dict:
    """Send a mouse-wheel scroll to a window WITHOUT focusing it (WM_MOUSEWHEEL)."""
    delta = int(clicks) * 120  # WHEEL_DELTA
    wp = (delta & 0xFFFF) << 16
    rc = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rc))
    lp = ((rc.bottom & 0xFFFF) << 16) | (rc.right & 0xFFFF)
    for _ in range(max(1, abs(int(clicks)))):
        step = 120 if clicks > 0 else -120
        _user32.PostMessageW(hwnd, WM_MOUSEWHEEL, ((step & 0xFFFF) << 16), lp)
        time.sleep(0.02)
    return {"ok": True, "clicks": int(clicks)}


def window_drag(hwnd: int, x1: int, y1: int, x2: int, y2: int,
                button: str = "left", steps: int = 14) -> dict:
    """Drag inside a window WITHOUT focusing it (client coordinates, interpolated)."""
    if button == "left":
        mk, down, up = MK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP
    else:
        mk, down, up = MK_RBUTTON, WM_RBUTTONDOWN, WM_RBUTTONUP
    lp1 = ((int(y1) & 0xFFFF) << 16) | (int(x1) & 0xFFFF)
    _user32.PostMessageW(hwnd, down, mk, lp1)
    for i in range(1, int(steps) + 1):
        x = int(x1 + (x2 - x1) * i / steps)
        y = int(y1 + (y2 - y1) * i / steps)
        lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        _user32.PostMessageW(hwnd, WM_MOUSEMOVE, mk, lp)
        time.sleep(0.01)
    lp2 = ((int(y2) & 0xFFFF) << 16) | (int(x2) & 0xFFFF)
    _user32.PostMessageW(hwnd, up, 0, lp2)
    return {"ok": True, "from": [x1, y1], "to": [x2, y2]}


_INPUT_CLASS_HINTS = ("edit", "richedit", "textbox", "textinput")


def pick_input_child(hwnd: int) -> int | None:
    """
    Find a text-input child control (Edit/RichEdit/etc.).
    Needed for WinUI apps (e.g. the new Notepad) where the top-level surface
    swallows synthetic messages — post to the child Edit control instead.
    """
    for c in list_children(hwnd):
        cls = c["class"].lower()
        if any(h in cls for h in _INPUT_CLASS_HINTS):
            return c["hwnd"]
    return None


def list_children(hwnd: int) -> list:
    """List child controls of a window (class name + title + hwnd)."""
    out = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(child, _lp):
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(child, cls, 256)
        out.append({"hwnd": child, "class": cls.value,
                    "title": _get_window_title(child)})
        return True

    cb = EnumProc(_cb)
    _user32.EnumChildWindows(hwnd, cb, 0)
    return out


# ---------------------------------------------------------------------------
# Modern-app input routing (WinUI / UWP / XAML)
#
# New-generation Windows apps (modern Notepad, Settings, ...) render a single
# DirectX surface: there are no classic Win32 child controls to post to, and
# the top-level surface silently swallows synthetic WM_* messages.
# probe_input_mode() classifies a window so the server can route input:
#   'focused'     window is already foreground (focused path is safe)
#   'invalid'     not a live top-level window / message queue unreachable
#   'uia'         modern surface detected — posted messages will be swallowed;
#                 caller should focus the window and use the SendInput path
#   'postmessage' classic Win32 app — background PostMessage path is fine
# ---------------------------------------------------------------------------

WM_NULL = 0x0000

_UIX_CLASS_MARKERS = ("applicationframewindow", "corewindow", "winui", "xaml")


def _looks_like_uix(hwnd: int) -> bool:
    """Heuristic: does this window host a modern (XAML/WinUI/UWP) surface?"""
    cls = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, cls, 256)
    if any(m in cls.value.lower() for m in _UIX_CLASS_MARKERS):
        return True
    try:
        # Top-level ApplicationFrameWindow hosts the real UI in a CoreWindow child
        return any("corewindow" in c["class"].lower()
                   or "winui" in c["class"].lower()
                   or "xaml" in c["class"].lower()
                   for c in list_children(hwnd))
    except Exception:
        return False


def probe_input_mode(hwnd: int) -> str:
    """Classify how a window best receives input (see section docstring)."""
    if not IS_WINDOWS:
        return "postmessage"
    if not _user32.IsWindow(hwnd):
        return "invalid"
    if _user32.GetForegroundWindow() == hwnd:
        return "focused"
    if _looks_like_uix(hwnd):
        return "uia"
    # Queue probe: WM_NULL fails when the message queue is unreachable
    if not _user32.PostMessageW(hwnd, WM_NULL, 0, 0):
        return "invalid"
    return "postmessage"


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """Convert window-client coordinates to screen coordinates (focused path)."""
    if not IS_WINDOWS:
        raise ValueError("client_to_screen is Windows-only")
    pt = wintypes.POINT(int(x), int(y))
    if not _user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        raise ValueError("ClientToScreen failed")
    return int(pt.x), int(pt.y)


# ---------------------------------------------------------------------------
# PlatformBackend binding
# ---------------------------------------------------------------------------

_BASE_CAPABILITIES = {
    "screen_capture": True, "ocr": "optional", "mouse_control": True,
    "keyboard_control": True, "window_enumeration": True,
    "background_input": "strong", "virtual_desktops": True,
    "game_mode": True,
}


class WindowsBackend(PlatformBackend):
    """Reference PlatformBackend implementation (Win32 + pyautogui).

    All state is module-level (as it always was in control.py), so every
    method binds to the module function as a staticmethod.
    """

    def get_capabilities(self) -> dict:
        return dict(_BASE_CAPABILITIES, platform="windows")

    # capture
    screen_size = staticmethod(screen_size)
    list_monitors = staticmethod(list_monitors)
    screenshot = staticmethod(screenshot)
    screenshot_jpeg = staticmethod(screenshot_jpeg)
    screenshot_scaled = staticmethod(screenshot_scaled)
    frame_diff = staticmethod(frame_diff)
    capture_window = staticmethod(capture_window)

    # mouse
    mouse_move = staticmethod(mouse_move)
    mouse_click = staticmethod(mouse_click)
    mouse_scroll = staticmethod(mouse_scroll)
    mouse_drag = staticmethod(mouse_drag)
    mouse_move_relative = staticmethod(mouse_move_relative)
    mouse_down = staticmethod(mouse_down)
    mouse_up = staticmethod(mouse_up)

    # keyboard
    assert_allowed = staticmethod(_assert_allowed)
    key_press = staticmethod(key_press)
    key_down = staticmethod(key_down)
    key_up = staticmethod(key_up)
    key_hotkey = staticmethod(key_hotkey)
    type_text = staticmethod(type_text)
    held_state = staticmethod(held_state)
    release_all = staticmethod(release_all)

    # windows
    list_windows = staticmethod(list_windows)
    get_focused_window = staticmethod(get_focused_window)
    focus_window = staticmethod(focus_window)
    close_window = staticmethod(close_window_safely)
    kill_process = staticmethod(kill_process)
    process_name = staticmethod(process_name)
    probe_input_mode = staticmethod(probe_input_mode)
    maximize_window = staticmethod(_maximize_window)
    set_topmost = staticmethod(_set_topmost)
    window_type_text = staticmethod(window_type_text)
    window_key = staticmethod(window_key)
    window_hotkey = staticmethod(window_hotkey)
    window_click = staticmethod(window_click)
    window_scroll = staticmethod(window_scroll)
    window_drag = staticmethod(window_drag)
    list_children = staticmethod(list_children)
    pick_input_child = staticmethod(pick_input_child)
    client_to_screen = staticmethod(client_to_screen)

    # game mode
    game_start = staticmethod(game_start)
    game_move = staticmethod(game_move)
    game_stop = staticmethod(game_stop)
    game_active = staticmethod(_game_active)
