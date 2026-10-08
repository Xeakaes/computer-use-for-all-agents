"""Linux backend — X11 input via xdotool, window management via wmctrl.

Construction fails closed on Wayland (UNSUPPORTED_DISPLAY_SERVER); without
X11 every action raises BACKEND_UNAVAILABLE while read-only getters stay
safe. ROADMAP Phase 5 task 9 replaces the remaining game-mode stubs.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import threading
import time

import mss
from PIL import Image

from backends import imageops
from backends.forbidden import assert_forbidden
from core.backends import PlatformBackend
from core.errors import ApiError

_PLATFORM = "linux"

_CAPABILITIES = {
    "platform": "linux",
    "screen_capture": True,
    "ocr": "optional",
    "mouse_control": True,
    "keyboard_control": True,
    "window_enumeration": True,
    "background_input": False,
    "virtual_desktops": False,
    "game_mode": True,
}


def display_server() -> str:
    """Active display server: "wayland" | "x11" | "none"."""
    session = os.environ.get("XDG_SESSION_TYPE") or ""
    if session == "wayland" or (os.environ.get("WAYLAND_DISPLAY")
                                and session != "x11"):
        return "wayland"
    if session == "x11" or os.environ.get("DISPLAY"):
        return "x11"
    return "none"


def _unavailable(action: str) -> ApiError:
    return ApiError("BACKEND_UNAVAILABLE",
                    f"{action} is not implemented on the {_PLATFORM} backend",
                    status=501, platform=_PLATFORM, action=action,
                    remediation="not implemented on this backend yet")


def run_x11(cmd: list[str], timeout: float = 5.0) -> str:
    """Run an X11 helper (xdotool/wmctrl/...) and return its stdout."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        raise ApiError("BACKEND_UNAVAILABLE",
                       f"{cmd[0]} is not installed or not on PATH",
                       status=501, platform=_PLATFORM,
                       remediation="sudo apt install xdotool wmctrl")
    except subprocess.TimeoutExpired:
        raise ApiError("OPERATION_TIMEOUT",
                       f"{cmd[0]} timed out after {timeout}s",
                       status=504, platform=_PLATFORM, action=cmd[0])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout


def type_segments(text: str) -> list[str]:
    """Split text into segments typed separately (Return goes between)."""
    return text.split("\n")


KEYMAP = {
    "enter": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "space": "space",
    "tab": "Tab",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "backspace": "BackSpace",
    "delete": "Delete",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "win": "Super_L",
    "home": "Home",
    "end": "End",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
}


def xdotool_key(name: str) -> str:
    """Map an API key name to its xdotool keysym (pass-through fallback)."""
    return KEYMAP.get(name.lower(), name)


_BUTTON_CODES = {"left": "1", "middle": "2", "right": "3"}

_held_lock = threading.Lock()
_held_keys: dict[str, str] = {}
_held_buttons: dict[str, str] = {}


def _button_code(button: str) -> str:
    try:
        return _BUTTON_CODES[button]
    except KeyError:
        raise ValueError(f"unknown mouse button: {button!r}") from None


def _mouse_position() -> tuple:
    out = run_x11(["xdotool", "getmouselocation", "--shell"])
    coords = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            coords[key] = value
    return int(coords.get("X", 0)), int(coords.get("Y", 0))


def mouse_move(x: int, y: int, duration: float = 0.15) -> None:
    """Move to absolute (x, y), interpolating over <=20 steps.

    A no-op when the pointer is already at the target: a zero-delta
    `mousemove --sync` blocks ~15s on xdotool 3.20160805 waiting for a
    motion event that never comes, which exceeds run_x11's 5s budget.
    Floor rounding can land the stepped loop exactly on the target
    before the last step (small negative deltas), so the final --sync
    is skipped when the steps already reached it; every executed
    --sync therefore carries a non-zero delta.
    """
    x0, y0 = _mouse_position()
    if (x0, y0) == (x, y):
        return
    if duration <= 0:
        run_x11(["xdotool", "mousemove", "--sync", str(x), str(y)])
        return
    steps = min(20, max(1, int(duration * 100)))
    delay = duration / steps
    prev_x, prev_y = x0, y0
    for step in range(1, steps):
        target_x = x0 + (x - x0) * step // steps
        target_y = y0 + (y - y0) * step // steps
        if (target_x, target_y) != (prev_x, prev_y):
            run_x11(["xdotool", "mousemove_relative", "--sync", "--",
                     str(target_x - prev_x), str(target_y - prev_y)])
        prev_x, prev_y = target_x, target_y
        time.sleep(delay)
    if (prev_x, prev_y) != (x, y):
        run_x11(["xdotool", "mousemove", "--sync", str(x), str(y)])


def mouse_click(x, y, button: str = "left", clicks: int = 1) -> None:
    """Move to (x, y) then click."""
    mouse_move(x, y, duration=0)
    if clicks >= 1:
        run_x11(["xdotool", "click", "--repeat", str(clicks),
                 _button_code(button)])


def mouse_scroll(clicks: int, x=None, y=None) -> None:
    """Scroll the wheel: positive = up (button 4), negative = down (5)."""
    if not clicks:
        return
    if x is not None and y is not None:
        mouse_move(x, y, duration=0)
    button = "4" if clicks > 0 else "5"
    run_x11(["xdotool", "click", "--repeat", str(abs(clicks)), button])


def mouse_drag(x1: int, y1: int, x2: int, y2: int,
               duration: float = 0.3, button: str = "left") -> None:
    """Drag from (x1, y1) to (x2, y2) with the button held."""
    mouse_move(x1, y1, duration=0)
    mouse_down(button)
    mouse_move(x2, y2, duration)
    mouse_up(button)


def mouse_move_relative(dx: int, dy: int) -> None:
    """Relative pointer move (negative values need the '--' separator)."""
    run_x11(["xdotool", "mousemove_relative", "--sync", "--",
             str(dx), str(dy)])


def mouse_down(button: str = "left") -> None:
    """Press and hold a mouse button (tracked for release_all)."""
    run_x11(["xdotool", "mousedown", _button_code(button)])
    with _held_lock:
        _held_buttons[button] = "x11"


def mouse_up(button: str = "left") -> None:
    """Release a held mouse button."""
    run_x11(["xdotool", "mouseup", _button_code(button)])
    with _held_lock:
        _held_buttons.pop(button, None)


_POLICY_ALIASES = {"super": "win", "meta": "win"}


def _flatten_key(name: str) -> list[str]:
    """Split an xdotool chord string into parts; a lone '+' is a key name."""
    if len(name) > 1 and "+" in name:
        return name.split("+")
    return [name]


def _assert_allowed_keys(keys) -> None:
    """Enforce the shared forbidden-key policy on flattened key names.

    Every name is aliased to the policy's view (super/meta are win) before
    the check, so those keys are unsendable in any context — lone, chord,
    or any combo — exactly like win on Windows (strict parity). The names
    sent to xdotool are never rewritten for keys that pass policy.
    """
    flat = []
    for key in keys:
        flat.extend(_flatten_key(key))
    assert_forbidden([_POLICY_ALIASES.get(part.lower(), part)
                      for part in flat])


def _assert_single_allowed(key: str) -> None:
    """Refuse chord strings for single-key actions, then apply the policy."""
    if len(_flatten_key(key)) > 1:
        raise ValueError(f"{key!r} is a chord; chords go through the "
                         f"hotkey action")
    _assert_allowed_keys([key])


def key_press(key: str) -> None:
    """Press and release a single key."""
    _assert_single_allowed(key)
    run_x11(["xdotool", "key", xdotool_key(key)])


def key_down(key: str) -> None:
    """Hold a key down (tracked for release_all / watchdog)."""
    _assert_single_allowed(key)
    run_x11(["xdotool", "keydown", xdotool_key(key)])
    with _held_lock:
        _held_keys[key] = "x11"


def key_up(key: str) -> None:
    """Release a held key."""
    _assert_single_allowed(key)
    run_x11(["xdotool", "keyup", xdotool_key(key)])
    with _held_lock:
        _held_keys.pop(key, None)


def key_hotkey(*keys: str) -> None:
    """Press a chord in one xdotool call (e.g. key_hotkey('ctrl', 'c'))."""
    _assert_allowed_keys(keys)
    chord = "+".join(xdotool_key(key) for key in keys)
    run_x11(["xdotool", "key", chord])


def type_text(text: str, interval: float = 0.03) -> None:
    """Type text: one xdotool type per line segment, Return between."""
    delay_ms = int(round(interval * 1000))
    for index, segment in enumerate(type_segments(text)):
        if index:
            run_x11(["xdotool", "key", "Return"])
        if segment:
            run_x11(["xdotool", "type", "--delay",
                     str(delay_ms), "--", segment])


def held_state() -> dict:
    """Snapshot of currently held keys/buttons (never raises)."""
    with _held_lock:
        return {"keys": sorted(_held_keys), "buttons": sorted(_held_buttons)}


def release_all() -> dict:
    """Release everything held, per recorded source (never raises)."""
    with _held_lock:
        keys = list(_held_keys.items())
        buttons = list(_held_buttons.items())
        _held_keys.clear()
        _held_buttons.clear()
    released = []
    for name, source in keys:
        try:
            if source != "x11":
                raise RuntimeError(f"unknown input source: {source}")
            run_x11(["xdotool", "keyup", xdotool_key(name)])
            released.append(name)
        except Exception as exc:
            released.append(f"{name} (release failed: {exc})")
    for name, source in buttons:
        try:
            if source != "x11":
                raise RuntimeError(f"unknown input source: {source}")
            run_x11(["xdotool", "mouseup", _button_code(name)])
            released.append(f"mouse:{name}")
        except Exception as exc:
            released.append(f"mouse:{name} (release failed: {exc})")
    return {"ok": True, "released": released}


# ---------------------------------------------------------------------------
# windows — wmctrl/xwininfo parsing and helpers (Task 7)
# ---------------------------------------------------------------------------

def parse_wmctrl(line: str):
    """Parse one line of `wmctrl -lGpx` output; None when malformed (pure).

    Column layout per wmctrl(1): WINDOW DESKTOP PID X Y W H WM_CLASS
    CLIENT-MACHINE TITLE. The client-machine column is always present and
    the title is the remainder of the line, so it may contain spaces and
    any UTF-8 text.
    """
    parts = line.split(None, 9)
    if len(parts) < 9 or not parts[0].startswith("0x"):
        return None
    try:
        hwnd = int(parts[0], 16)
        desktop = int(parts[1])
        pid = int(parts[2])
        x, y, w, h = (int(parts[i]) for i in (3, 4, 5, 6))
    except ValueError:
        return None
    return {"hwnd": hwnd, "desktop": desktop, "rect": [x, y, x + w, y + h],
            "pid": pid, "wm_class": parts[7],
            "title": parts[9] if len(parts) == 10 else ""}


_TREE_LINE = re.compile(
    r'^\s+(?P<hwnd>0x[0-9a-fA-F]+)\s+(?:"(?P<title>[^"]*)"|\(has no name\)):'
    r'\s+\((?P<cls>[^)]*)\)')


def parse_xwininfo_tree(text: str) -> list:
    """Parse the child lines of `xwininfo -id <id> -tree` (pure).

    Each child line carries its window id, its quoted title (or
    "(has no name)"), and its WM_CLASS pair in parentheses; header lines
    ("xwininfo:", "Root window id:", "N child:") are skipped. Returns all
    descendants, mirroring Win32 EnumChildWindows.
    """
    children = []
    for line in text.splitlines():
        match = _TREE_LINE.match(line)
        if not match:
            continue
        quoted = re.findall(r'"([^"]*)"', match.group("cls"))
        children.append({"hwnd": int(match.group("hwnd"), 16),
                         "class": quoted[-1] if quoted else "",
                         "title": match.group("title") or ""})
    return children


def process_name(pid: int) -> "str | None":
    """Resolve a pid to its process name via /proc/<pid>/comm (None if gone)."""
    try:
        with open(f"/proc/{int(pid)}/comm", encoding="utf-8",
                  errors="replace") as fh:
            name = fh.read().strip()
    except (OSError, ValueError):
        return None
    return name or None


# X11 keysym values (X11/keysymdef.h) for every KEYMAP target plus the
# modifier names xdotool accepts as pass-through aliases. Verified against
# Xlib.XK.string_to_keysym.
_KEYSYM_NAMES = {
    "space": 0x0020, "Tab": 0xFF09, "BackSpace": 0xFF08,
    "Return": 0xFF0D, "Escape": 0xFF1B, "Delete": 0xFFFF,
    "Home": 0xFF50, "Left": 0xFF51, "Up": 0xFF52, "Right": 0xFF53,
    "Down": 0xFF54, "Page_Up": 0xFF55, "Page_Down": 0xFF56, "End": 0xFF57,
    "Shift_L": 0xFFE1, "Shift_R": 0xFFE2, "Control_L": 0xFFE3,
    "Control_R": 0xFFE4, "Alt_L": 0xFFE9, "Alt_R": 0xFFEA,
    "Super_L": 0xFFEB, "Super_R": 0xFFEC,
    "F1": 0xFFBE, "F2": 0xFFBF, "F3": 0xFFC0, "F4": 0xFFC1,
    "F5": 0xFFC2, "F6": 0xFFC3, "F7": 0xFFC4, "F8": 0xFFC5,
    "F9": 0xFFC6, "F10": 0xFFC7, "F11": 0xFFC8, "F12": 0xFFC9,
}

_KEYSYM_ALIASES = {"ctrl": "Control_L", "control": "Control_L",
                   "alt": "Alt_L", "shift": "Shift_L", "super": "Super_L"}


def keysym_for(key: str) -> int:
    """Resolve an API key name to its X11 keysym int (pure).

    Unknown names raise ValueError (vk_from_name parity: the server maps
    that to a 400).
    """
    name = xdotool_key(key)
    name = _KEYSYM_ALIASES.get(name.lower(), name)
    if name in _KEYSYM_NAMES:
        return _KEYSYM_NAMES[name]
    if len(name) == 1:
        code = ord(name)
        return code if code < 256 else 0x01000000 | code
    raise ValueError(f"unknown key name: {key!r}")


def _wmctrl_lines() -> list:
    """`wmctrl -lGpx` lines; a window closing mid-listing aborts the whole
    run with BadWindow, so retry the race out before giving up."""
    last = None
    for attempt in range(3):
        try:
            return run_x11(["wmctrl", "-lGpx"]).splitlines()
        except RuntimeError as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.05)
    raise last


def _window_info(hwnd: int):
    """wmctrl entry for one hwnd, None when absent (raises on tool failure)."""
    for line in _wmctrl_lines():
        info = parse_wmctrl(line)
        if info and info["hwnd"] == hwnd:
            return info
    return None


def _window_present(hwnd: int) -> bool:
    """True unless wmctrl proves the window gone (conservative on errors)."""
    try:
        return _window_info(hwnd) is not None
    except (ApiError, RuntimeError):
        return True


def _active_window():
    """Id of the X11 active window, or None when it cannot be parsed."""
    raw = run_x11(["xdotool", "getactivewindow"]).strip()
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        return None


_ABS_X = re.compile(r"Absolute upper-left X:\s*(-?\d+)")
_ABS_Y = re.compile(r"Absolute upper-left Y:\s*(-?\d+)")


def _xwininfo_origin(text: str):
    """Root-absolute (x, y) origin of the queried window, or None."""
    mx, my = _ABS_X.search(text), _ABS_Y.search(text)
    if not mx or not my:
        return None
    return int(mx.group(1)), int(my.group(1))


_WIDTH = re.compile(r"^\s+Width:\s*(\d+)", re.M)
_HEIGHT = re.compile(r"^\s+Height:\s*(\d+)", re.M)


def _xwininfo_size(text: str):
    """Pixel (width, height) of the queried window, or None (pure)."""
    mw, mh = _WIDTH.search(text), _HEIGHT.search(text)
    if not mw or not mh:
        return None
    return int(mw.group(1)), int(mh.group(1))


_INPUT_CLASS_HINTS = ("edit", "richedit", "textbox", "textinput",
                      "entry", "field")


class LinuxBackend(PlatformBackend):
    """X11 implementation of PlatformBackend; design spec: Phase 5."""

    def __init__(self) -> None:
        if display_server() == "wayland":
            raise ApiError(
                "UNSUPPORTED_DISPLAY_SERVER",
                "Wayland display server detected; the Linux backend "
                "requires an X11 session",
                status=501, platform=_PLATFORM,
                remediation="start your desktop session in X11 mode "
                            "(e.g. 'Cinnamon on X11' at the login screen)")

    def get_capabilities(self) -> dict:
        return dict(_CAPABILITIES)

    def require_x(self, action: str) -> None:
        """Fail closed unless an X11 display is available."""
        if display_server() != "x11":
            raise ApiError("BACKEND_UNAVAILABLE",
                           f"{action} requires an X11 display",
                           status=501, platform=_PLATFORM, action=action,
                           remediation="X11 DISPLAY not available")

    # capture
    def screen_size(self, monitor: int = 1) -> tuple:
        """Width/height of one monitor (mss index, 1 = first physical)."""
        self.require_x("screen_size")
        with mss.MSS() as sct:
            mon = sct.monitors[monitor]
            return mon["width"], mon["height"]

    def list_monitors(self) -> list:
        """Physical monitors with Windows-parity fields (1-based ids)."""
        self.require_x("list_monitors")
        monitors = []
        with mss.MSS() as sct:
            for i, mon in enumerate(sct.monitors):
                if i == 0:  # skip the virtual all-in-one monitor
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

    def screenshot(self, monitor: int = 1, region=None):
        """Capture a monitor or an (x, y, w, h) region as an RGB Image."""
        self.require_x("screenshot")
        with mss.MSS() as sct:
            if region is None:
                mon = sct.monitors[monitor]
            else:
                x, y, w, h = region
                mon = {"left": x, "top": y, "width": w, "height": h}
            raw = sct.grab(mon)
            return Image.frombytes("RGB", raw.size, raw.rgb)

    def screenshot_jpeg(self, monitor: int = 1, region=None,
                        quality: int = 80) -> bytes:
        """JPEG bytes via imageops.encode_jpeg (faster for streaming)."""
        self.require_x("screenshot_jpeg")
        return imageops.encode_jpeg(self.screenshot(monitor, region),
                                    quality)

    def screenshot_scaled(self, monitor: int = 1, region=None,
                          scale: float = 1.0, grayscale: bool = False):
        """Scaled/grayscale frame via imageops.scale_image."""
        self.require_x("screenshot_scaled")
        return imageops.scale_image(self.screenshot(monitor, region),
                                    scale, grayscale)

    def frame_diff(self, prev, cur) -> dict:
        """Structured diff between two frames (delegates to imageops)."""
        self.require_x("frame_diff")
        return imageops.frame_diff(prev, cur)

    def capture_window(self, hwnd: int, client_only: bool = False):
        """Capture a window's client area as an RGB Image.

        Always the client area for both `client_only` values: X11
        decorations live in a separate reparenting frame window that
        these primitives cannot reach (on Windows, client_only=False
        includes that frame). The captured origin therefore matches
        client_to_screen exactly, so OCR region coordinates do not drift.

        Uses ImageMagick `import -window <id>` when installed; the
        compositor usually yields the window's own pixels even when it
        is occluded. Without it, falls back to an mss grab of the
        window's screen rectangle — that captures the screen as-is, so
        an occluded window shows whatever is on top. Failures raise
        RuntimeError so the server answers 409.
        """
        self.require_x("capture_window")
        if shutil.which("import"):
            try:
                proc = subprocess.run(
                    ["import", "-window", hex(hwnd), "png:-"],
                    capture_output=True, timeout=5.0)
            except FileNotFoundError:
                raise RuntimeError(
                    f"import disappeared from PATH while capturing "
                    f"{hex(hwnd)}") from None
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"import timed out capturing {hex(hwnd)}") from exc
            if proc.returncode != 0:
                detail = proc.stderr.decode("utf-8", "replace").splitlines()
                raise RuntimeError(
                    f"import failed for window {hex(hwnd)}: "
                    f"{detail[0] if detail else 'no error output'}")
            try:
                with Image.open(io.BytesIO(proc.stdout)) as img:
                    return img.convert("RGB")
            except OSError as exc:
                raise RuntimeError(
                    f"import returned an unreadable image for "
                    f"{hex(hwnd)}: {exc}") from exc
        try:
            text = run_x11(["xwininfo", "-id", hex(hwnd)])
        except RuntimeError as exc:
            raise RuntimeError(
                f"window {hex(hwnd)} cannot be captured: {exc}") from exc
        origin, size = _xwininfo_origin(text), _xwininfo_size(text)
        if origin is None or size is None:
            raise RuntimeError(f"window {hex(hwnd)} has no usable geometry")
        left, top = origin
        width, height = size
        if width <= 0 or height <= 0:
            raise RuntimeError(
                f"window {hex(hwnd)} has invalid size {width}x{height}")
        with mss.MSS() as sct:
            raw = sct.grab({"left": left, "top": top,
                            "width": width, "height": height})
        return Image.frombytes("RGB", raw.size, raw.rgb)

    # mouse
    def mouse_move(self, x: int, y: int, duration: float = 0.15) -> None:
        self.require_x("mouse_move")
        mouse_move(x, y, duration)

    def mouse_click(self, x, y, button: str = "left", clicks: int = 1) -> None:
        self.require_x("mouse_click")
        mouse_click(x, y, button, clicks)

    def mouse_scroll(self, clicks: int, x=None, y=None) -> None:
        self.require_x("mouse_scroll")
        mouse_scroll(clicks, x, y)

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   duration: float = 0.3, button: str = "left") -> None:
        self.require_x("mouse_drag")
        mouse_drag(x1, y1, x2, y2, duration, button)

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        self.require_x("mouse_move_relative")
        mouse_move_relative(dx, dy)

    def mouse_down(self, button: str = "left") -> None:
        self.require_x("mouse_down")
        mouse_down(button)

    def mouse_up(self, button: str = "left") -> None:
        self.require_x("mouse_up")
        mouse_up(button)

    # keyboard
    def assert_allowed(self, keys) -> None:
        _assert_allowed_keys(keys)

    def key_press(self, key: str) -> None:
        self.require_x("key_press")
        key_press(key)

    def key_down(self, key: str) -> None:
        self.require_x("key_down")
        key_down(key)

    def key_up(self, key: str) -> None:
        self.require_x("key_up")
        key_up(key)

    def key_hotkey(self, *keys: str) -> None:
        self.require_x("key_hotkey")
        key_hotkey(*keys)

    def type_text(self, text: str, interval: float = 0.03) -> None:
        self.require_x("type_text")
        type_text(text, interval)

    held_state = staticmethod(held_state)
    release_all = staticmethod(release_all)

    # windows
    def list_windows(self) -> list:
        self.require_x("list_windows")
        lines = _wmctrl_lines()
        try:
            active = _active_window()
        except (ApiError, RuntimeError):
            active = None
        windows = []
        for line in lines:
            info = parse_wmctrl(line)
            if not info or not info["title"]:
                continue
            windows.append({
                "hwnd": info["hwnd"],
                "title": info["title"],
                "process": process_name(info["pid"]) or "?",
                "pid": info["pid"],
                "focused": active is not None and info["hwnd"] == active,
                "rect": info["rect"],
            })
        return windows

    def get_focused_window(self):
        self.require_x("get_focused_window")
        try:
            active = _active_window()
        except (ApiError, RuntimeError):
            return None
        if active is None:
            return None
        for window in self.list_windows():
            if window["hwnd"] == active:
                return window
        return None

    def focus_window(self, hwnd: int) -> None:
        self.require_x("focus_window")
        run_x11(["xdotool", "windowactivate", "--sync", hex(hwnd)])
        try:
            active = _active_window()
        except (ApiError, RuntimeError) as exc:
            raise RuntimeError(
                f"focus_window: cannot verify the active window: {exc}"
            ) from exc
        if active != hwnd:
            raise RuntimeError(
                f"focus_window: hwnd {hwnd} did not take focus "
                f"(active window is {active})")

    def close_window(self, hwnd: int, expect_title=None,
                     expect_process=None) -> dict:
        """Close after title/process verification; never raises on mismatch.

        Every refusal path returns before the mutating `wmctrl -ic`, and a
        missing display is refused before any X call at all.
        """
        if display_server() != "x11":
            return {"ok": False,
                    "error": "no X11 display; cannot verify the window; "
                             "refusing to close"}
        try:
            info = _window_info(hwnd)
        except (ApiError, RuntimeError) as exc:
            return {"ok": False,
                    "error": f"cannot verify window {hex(hwnd)}: {exc}"}
        title = info["title"] if info else ""
        if not title:
            return {"ok": False,
                    "error": "window has no title; refusing to close"}
        if expect_title and expect_title.lower() not in title.lower():
            return {"ok": False,
                    "error": f"title mismatch: expected '{expect_title}', "
                             f"got '{title}'"}
        if expect_process:
            name = process_name(info["pid"])
            if (name or "").lower() != expect_process.lower():
                return {"ok": False,
                        "error": f"process mismatch: expected "
                                 f"'{expect_process}', got '{name or '?'}'"}
        try:
            run_x11(["wmctrl", "-ic", hex(hwnd)])
        except RuntimeError:
            pass  # the window vanished between verification and close
        deadline = time.monotonic() + 1.0
        closed = not _window_present(hwnd)
        while not closed and time.monotonic() < deadline:
            time.sleep(0.1)
            closed = not _window_present(hwnd)
        return {"ok": True, "closed": closed, "title": title,
                "note": "" if closed else
                        "window still open (app may be showing a save "
                        "prompt); use kill with the pid if it must go"}

    def kill_process(self, pid: int) -> dict:
        """SIGKILL a pid after the guard checks, before any call is made."""
        if pid <= 1:
            return {"ok": False,
                    "output": f"refusing to kill pid {pid} (system process)"}
        if pid == os.getpid():
            return {"ok": False, "output": "refusing to kill own process"}
        try:
            output = run_x11(["kill", "-9", str(pid)]).strip()
        except RuntimeError as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": output}

    def process_name(self, pid: int) -> "str | None":
        return process_name(pid)

    def probe_input_mode(self, hwnd: int) -> str:
        """'focused' for a live window, 'invalid' otherwise; never raises."""
        if display_server() != "x11":
            return "invalid"
        try:
            return "focused" if _window_info(hwnd) is not None else "invalid"
        except Exception:
            return "invalid"

    def maximize_window(self, hwnd: int) -> None:
        self.require_x("maximize_window")
        run_x11(["wmctrl", "-ir", hex(hwnd), "-b",
                 "add,maximized_vert,maximized_horz"])

    def set_topmost(self, hwnd: int, topmost: bool) -> None:
        self.require_x("set_topmost")
        run_x11(["wmctrl", "-ir", hex(hwnd), "-b",
                 "add,above" if topmost else "remove,above"])

    def window_type_text(self, hwnd: int, text: str) -> dict:
        self.require_x("window_type_text")
        self.focus_window(hwnd)
        type_text(text)
        return {"ok": True, "chars": len(text)}

    def window_key(self, hwnd: int, key: str) -> dict:
        _assert_single_allowed(key)
        vk = keysym_for(key)
        self.require_x("window_key")
        self.focus_window(hwnd)
        run_x11(["xdotool", "key", xdotool_key(key)])
        return {"ok": True, "vk": vk}

    def window_hotkey(self, hwnd: int, keys) -> dict:
        keys = list(keys)
        _assert_allowed_keys(keys)
        vks = [keysym_for(key) for key in keys]
        self.require_x("window_hotkey")
        self.focus_window(hwnd)
        if keys:
            run_x11(["xdotool", "key",
                     "+".join(xdotool_key(key) for key in keys)])
        return {"ok": True, "vk": vks[0] if vks else None, "vks": vks}

    def window_click(self, hwnd: int, x: int, y: int,
                     button: str = "left", clicks: int = 1) -> dict:
        self.require_x("window_click")
        self.focus_window(hwnd)
        screen_x, screen_y = self.client_to_screen(hwnd, x, y)
        mouse_click(screen_x, screen_y, button, int(clicks))
        return {"ok": True, "x": x, "y": y, "button": button,
                "clicks": int(clicks)}

    def window_scroll(self, hwnd: int, clicks: int) -> dict:
        self.require_x("window_scroll")
        self.focus_window(hwnd)
        center = None
        try:
            info = _window_info(hwnd)
        except (ApiError, RuntimeError):
            info = None
        if info:
            left, top, right, bottom = info["rect"]
            center = ((left + right) // 2, (top + bottom) // 2)
        mouse_scroll(int(clicks), *(center or (None, None)))
        return {"ok": True, "clicks": int(clicks)}

    def window_drag(self, hwnd: int, x1: int, y1: int, x2: int, y2: int,
                    button: str = "left") -> dict:
        self.require_x("window_drag")
        self.focus_window(hwnd)
        start = self.client_to_screen(hwnd, x1, y1)
        end = self.client_to_screen(hwnd, x2, y2)
        mouse_drag(start[0], start[1], end[0], end[1], button=button)
        return {"ok": True, "from": [x1, y1], "to": [x2, y2]}

    def list_children(self, hwnd: int) -> list:
        self.require_x("list_children")
        try:
            text = run_x11(["xwininfo", "-id", hex(hwnd), "-tree"])
        except RuntimeError:
            return []
        return parse_xwininfo_tree(text)

    def pick_input_child(self, hwnd: int) -> "int | None":
        for child in self.list_children(hwnd):
            cls = child["class"].lower()
            if any(hint in cls for hint in _INPUT_CLASS_HINTS):
                return child["hwnd"]
        return None

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple:
        self.require_x("client_to_screen")
        try:
            text = run_x11(["xwininfo", "-id", hex(hwnd)])
        except RuntimeError as exc:
            raise ValueError(f"window {hex(hwnd)} is gone: {exc}") from exc
        origin = _xwininfo_origin(text)
        if origin is None:
            raise ValueError(f"window {hex(hwnd)} has no geometry")
        return origin[0] + x, origin[1] + y

    # game mode
    def game_start(self, sensitivity: int = 12) -> dict:
        self.require_x("game_start")
        raise _unavailable("game_start")

    def game_move(self, dx: int, dy: int, sensitivity: int = 12) -> dict:
        self.require_x("game_move")
        raise _unavailable("game_move")

    def game_stop(self) -> dict:
        self.require_x("game_stop")
        raise _unavailable("game_stop")

    def game_active(self) -> bool:
        return False
