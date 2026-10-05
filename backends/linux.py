"""Linux backend — X11 skeleton with fail-fast Wayland detection.

Construction fails closed on Wayland (UNSUPPORTED_DISPLAY_SERVER); without
X11 every action raises BACKEND_UNAVAILABLE while read-only getters stay
safe. ROADMAP Phase 5 tasks 6-9 replace the stub bodies group by group
(xdotool/wmctrl input, window management, mss capture, uinput game mode).
"""
from __future__ import annotations

import os

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
        raise _unavailable("mouse_move")

    def mouse_click(self, x, y, button: str = "left", clicks: int = 1) -> None:
        self.require_x("mouse_click")
        raise _unavailable("mouse_click")

    def mouse_scroll(self, clicks: int, x=None, y=None) -> None:
        self.require_x("mouse_scroll")
        raise _unavailable("mouse_scroll")

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   duration: float = 0.3, button: str = "left") -> None:
        self.require_x("mouse_drag")
        raise _unavailable("mouse_drag")

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        self.require_x("mouse_move_relative")
        raise _unavailable("mouse_move_relative")

    def mouse_down(self, button: str = "left") -> None:
        self.require_x("mouse_down")
        raise _unavailable("mouse_down")

    def mouse_up(self, button: str = "left") -> None:
        self.require_x("mouse_up")
        raise _unavailable("mouse_up")

    # keyboard
    def assert_allowed(self, keys) -> None:
        assert_forbidden(keys)

    def key_press(self, key: str) -> None:
        self.require_x("key_press")
        raise _unavailable("key_press")

    def key_down(self, key: str) -> None:
        self.require_x("key_down")
        raise _unavailable("key_down")

    def key_up(self, key: str) -> None:
        self.require_x("key_up")
        raise _unavailable("key_up")

    def key_hotkey(self, *keys: str) -> None:
        self.require_x("key_hotkey")
        raise _unavailable("key_hotkey")

    def type_text(self, text: str, interval: float = 0.03) -> None:
        self.require_x("type_text")
        raise _unavailable("type_text")

    def held_state(self) -> dict:
        return {"keys": [], "buttons": []}   # nothing held yet

    def release_all(self) -> dict:
        return {"ok": True, "released": []}

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
