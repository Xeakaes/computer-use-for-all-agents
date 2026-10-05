"""Linux backend — X11 input via xdotool with fail-fast Wayland detection.

Construction fails closed on Wayland (UNSUPPORTED_DISPLAY_SERVER); without
X11 every action raises BACKEND_UNAVAILABLE while read-only getters stay
safe. ROADMAP Phase 5 tasks 7-9 replace the remaining stub bodies group by
group (wmctrl window management, mss capture, uinput game mode).
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

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
    The stepped loop stops one step short so the final --sync always
    carries the remaining delta.
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


def key_press(key: str) -> None:
    """Press and release a single key."""
    assert_forbidden([key])
    run_x11(["xdotool", "key", xdotool_key(key)])


def key_down(key: str) -> None:
    """Hold a key down (tracked for release_all / watchdog)."""
    assert_forbidden([key])
    run_x11(["xdotool", "keydown", xdotool_key(key)])
    with _held_lock:
        _held_keys[key] = "x11"


def key_up(key: str) -> None:
    """Release a held key."""
    assert_forbidden([key])
    run_x11(["xdotool", "keyup", xdotool_key(key)])
    with _held_lock:
        _held_keys.pop(key, None)


def key_hotkey(*keys: str) -> None:
    """Press a chord in one xdotool call (e.g. key_hotkey('ctrl', 'c'))."""
    assert_forbidden(list(keys))
    chord = "+".join(xdotool_key(key) for key in keys)
    run_x11(["xdotool", "key", chord])


def type_text(text: str, interval: float = 0.03) -> None:
    """Type text: one xdotool type per line segment, Return between."""
    delay_ms = int(round(interval * 1000))
    for index, segment in enumerate(type_segments(text)):
        if index:
            run_x11(["xdotool", "key", "Return"])
        if segment:
            run_x11(["xdotool", "type", "--clearmodifiers", "--delay",
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


class LinuxBackend(PlatformBackend):
    """X11-only skeleton; see the Phase 5 design spec for the plan."""

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
        self.require_x("screen_size")
        raise _unavailable("screen_size")

    def list_monitors(self) -> list:
        self.require_x("list_monitors")
        raise _unavailable("list_monitors")

    def screenshot(self, monitor: int = 1, region=None):
        self.require_x("screenshot")
        raise _unavailable("screenshot")

    def screenshot_jpeg(self, monitor: int = 1, region=None,
                        quality: int = 80) -> bytes:
        self.require_x("screenshot_jpeg")
        raise _unavailable("screenshot_jpeg")

    def screenshot_scaled(self, monitor: int = 1, region=None,
                          scale: float = 1.0, grayscale: bool = False):
        self.require_x("screenshot_scaled")
        raise _unavailable("screenshot_scaled")

    def frame_diff(self, prev, cur) -> dict:
        self.require_x("frame_diff")
        raise _unavailable("frame_diff")

    def capture_window(self, hwnd: int, client_only: bool = False):
        self.require_x("capture_window")
        raise _unavailable("capture_window")

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
        assert_forbidden(keys)

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
        raise _unavailable("list_windows")

    def get_focused_window(self):
        self.require_x("get_focused_window")
        raise _unavailable("get_focused_window")

    def focus_window(self, hwnd: int) -> None:
        self.require_x("focus_window")
        raise _unavailable("focus_window")

    def close_window(self, hwnd: int, expect_title=None,
                     expect_process=None) -> dict:
        self.require_x("close_window")
        raise _unavailable("close_window")

    def kill_process(self, pid: int) -> dict:
        self.require_x("kill_process")
        raise _unavailable("kill_process")

    def process_name(self, pid: int) -> "str | None":
        self.require_x("process_name")
        raise _unavailable("process_name")

    def probe_input_mode(self, hwnd: int) -> str:
        return "invalid"

    def maximize_window(self, hwnd: int) -> None:
        self.require_x("maximize_window")
        raise _unavailable("maximize_window")

    def set_topmost(self, hwnd: int, topmost: bool) -> None:
        self.require_x("set_topmost")
        raise _unavailable("set_topmost")

    def window_type_text(self, hwnd: int, text: str) -> dict:
        self.require_x("window_type_text")
        raise _unavailable("window_type_text")

    def window_key(self, hwnd: int, key: str) -> dict:
        self.require_x("window_key")
        raise _unavailable("window_key")

    def window_hotkey(self, hwnd: int, keys) -> dict:
        self.require_x("window_hotkey")
        raise _unavailable("window_hotkey")

    def window_click(self, hwnd: int, x: int, y: int,
                     button: str = "left", clicks: int = 1) -> dict:
        self.require_x("window_click")
        raise _unavailable("window_click")

    def window_scroll(self, hwnd: int, clicks: int) -> dict:
        self.require_x("window_scroll")
        raise _unavailable("window_scroll")

    def window_drag(self, hwnd: int, x1: int, y1: int, x2: int, y2: int,
                    button: str = "left") -> dict:
        self.require_x("window_drag")
        raise _unavailable("window_drag")

    def list_children(self, hwnd: int) -> list:
        self.require_x("list_children")
        raise _unavailable("list_children")

    def pick_input_child(self, hwnd: int) -> "int | None":
        self.require_x("pick_input_child")
        raise _unavailable("pick_input_child")

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple:
        self.require_x("client_to_screen")
        raise _unavailable("client_to_screen")

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
