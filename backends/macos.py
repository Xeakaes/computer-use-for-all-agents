"""macOS backend — fail-closed stub.

ROADMAP Phase 7 will implement real Quartz/CGEvent support with TCC permissions.
Until then every operation raises BACKEND_UNAVAILABLE so agents get an
honest refusal instead of half-working behavior.
"""
from __future__ import annotations

from backends.forbidden import assert_forbidden
from core.backends import PlatformBackend
from core.errors import ApiError

_PLATFORM = "macos"

_CAPABILITIES = {
    "platform": _PLATFORM,
    "screen_capture": None, "ocr": None, "mouse_control": None,
    "keyboard_control": None, "window_enumeration": None,
    "background_input": None, "virtual_desktops": None, "game_mode": None,
}


def _unavailable(action: str) -> ApiError:
    return ApiError("BACKEND_UNAVAILABLE",
                    f"{action} is not implemented on the {_PLATFORM} backend",
                    status=501, platform=_PLATFORM, action=action,
                    remediation="not implemented on this backend yet")


class MacOSBackend(PlatformBackend):
    """Fail-closed stub; see ROADMAP Phase 7 for the implementation plan."""

    def get_capabilities(self) -> dict:
        return dict(_CAPABILITIES)

    # capture
    def screen_size(self, monitor: int = 1) -> tuple:
        raise _unavailable("screen_size")

    def list_monitors(self) -> list:
        raise _unavailable("list_monitors")

    def screenshot(self, monitor: int = 1, region=None):
        raise _unavailable("screenshot")

    def screenshot_jpeg(self, monitor: int = 1, region=None,
                        quality: int = 80) -> bytes:
        raise _unavailable("screenshot_jpeg")

    def screenshot_scaled(self, monitor: int = 1, region=None,
                          scale: float = 1.0, grayscale: bool = False):
        raise _unavailable("screenshot_scaled")

    def frame_diff(self, prev, cur) -> dict:
        raise _unavailable("frame_diff")

    def capture_window(self, hwnd: int, client_only: bool = False):
        raise _unavailable("capture_window")

    # mouse
    def mouse_move(self, x: int, y: int, duration: float = 0.15) -> None:
        raise _unavailable("mouse_move")

    def mouse_click(self, x, y, button: str = "left", clicks: int = 1) -> None:
        raise _unavailable("mouse_click")

    def mouse_scroll(self, clicks: int, x=None, y=None) -> None:
        raise _unavailable("mouse_scroll")

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   duration: float = 0.3, button: str = "left") -> None:
        raise _unavailable("mouse_drag")

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        raise _unavailable("mouse_move_relative")

    def mouse_down(self, button: str = "left") -> None:
        raise _unavailable("mouse_down")

    def mouse_up(self, button: str = "left") -> None:
        raise _unavailable("mouse_up")

    # keyboard
    def assert_allowed(self, keys) -> None:
        assert_forbidden(keys)

    def key_press(self, key: str) -> None:
        raise _unavailable("key_press")

    def key_down(self, key: str) -> None:
        raise _unavailable("key_down")

    def key_up(self, key: str) -> None:
        raise _unavailable("key_up")

    def key_hotkey(self, *keys: str) -> None:
        raise _unavailable("key_hotkey")

    def type_text(self, text: str, interval: float = 0.03) -> None:
        raise _unavailable("type_text")

    def held_state(self) -> dict:
        return {"keys": [], "buttons": []}   # stub holds nothing

    def release_all(self) -> dict:
        return {"ok": True, "released": []}

    # windows
    def list_windows(self) -> list:
        raise _unavailable("list_windows")

    def get_focused_window(self):
        raise _unavailable("get_focused_window")

    def focus_window(self, hwnd: int) -> None:
        raise _unavailable("focus_window")

    def close_window(self, hwnd: int, expect_title=None,
                     expect_process=None) -> dict:
        raise _unavailable("close_window")

    def kill_process(self, pid: int) -> dict:
        raise _unavailable("kill_process")

    def process_name(self, pid: int) -> "str | None":
        raise _unavailable("process_name")

    def probe_input_mode(self, hwnd: int) -> str:
        return "invalid"

    def maximize_window(self, hwnd: int) -> None:
        raise _unavailable("maximize_window")

    def set_topmost(self, hwnd: int, topmost: bool) -> None:
        raise _unavailable("set_topmost")

    def window_type_text(self, hwnd: int, text: str) -> dict:
        raise _unavailable("window_type_text")

    def window_key(self, hwnd: int, key: str) -> dict:
        raise _unavailable("window_key")

    def window_hotkey(self, hwnd: int, keys) -> dict:
        raise _unavailable("window_hotkey")

    def window_click(self, hwnd: int, x: int, y: int,
                     button: str = "left", clicks: int = 1) -> dict:
        raise _unavailable("window_click")

    def window_scroll(self, hwnd: int, clicks: int) -> dict:
        raise _unavailable("window_scroll")

    def window_drag(self, hwnd: int, x1: int, y1: int, x2: int, y2: int,
                    button: str = "left") -> dict:
        raise _unavailable("window_drag")

    def list_children(self, hwnd: int) -> list:
        raise _unavailable("list_children")

    def pick_input_child(self, hwnd: int) -> "int | None":
        raise _unavailable("pick_input_child")

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple:
        raise _unavailable("client_to_screen")

    # game mode
    def game_start(self, sensitivity: int = 12) -> dict:
        raise _unavailable("game_start")

    def game_move(self, dx: int, dy: int, sensitivity: int = 12) -> dict:
        raise _unavailable("game_move")

    def game_stop(self) -> dict:
        raise _unavailable("game_stop")

    def game_active(self) -> bool:
        return False
