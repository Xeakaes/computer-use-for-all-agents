"""FakeBackend: deterministic PlatformBackend for tests.

Records every action in .calls instead of touching the OS. Serves a tiny
in-memory frame and a three-window fake desktop so the HTTP layer can be
exercised end-to-end without a screen (ROADMAP Phase 4 integration layer).
"""
from __future__ import annotations

from backends.forbidden import assert_forbidden
from core.backends import PlatformBackend
from core.errors import window_not_found

_W, _H = 64, 48

_FAKE_WINDOWS = [
    {"hwnd": 101, "title": "Untitled - Notepad", "process": "notepad.exe",
     "pid": 1001, "focused": True, "rect": [10, 10, 610, 460]},
    {"hwnd": 202, "title": "Settings", "process": "systemsettings.exe",
     "pid": 2002, "focused": False, "rect": [100, 100, 900, 700]},
    {"hwnd": 303, "title": "Minecraft", "process": "javaw.exe",
     "pid": 3003, "focused": False, "rect": [0, 0, 1280, 720]},
]


class FakeBackend(PlatformBackend):
    """In-memory backend; every action is logged to .calls."""

    def __init__(self) -> None:
        self.calls = []
        self._held_keys = set()
        self._held_buttons = set()
        self._closed = set()
        self._game = False

    def _record(self, method: str, args: tuple = (), kwargs: "dict | None" = None):
        entry = (method, args, kwargs or {})
        self.calls.append(entry)
        return entry

    def _live(self) -> list:
        return [dict(w, focused=(w["hwnd"] == 101))
                for w in _FAKE_WINDOWS if w["hwnd"] not in self._closed]

    def _require(self, hwnd: int) -> dict:
        win = next((w for w in _FAKE_WINDOWS if w["hwnd"] == hwnd), None)
        if win is None or hwnd in self._closed:
            raise window_not_found(f"hwnd={hwnd}")
        return win

    def _frame(self):
        from PIL import Image
        img = Image.new("RGB", (_W, _H), (30, 30, 30))
        img.putpixel((0, 0), (255, 255, 255))
        return img

    # -- capabilities ----------------------------------------------------
    def get_capabilities(self) -> dict:
        return {"platform": "fake", "screen_capture": True, "ocr": False,
                "mouse_control": True, "keyboard_control": True,
                "window_enumeration": True, "background_input": True,
                "virtual_desktops": False, "game_mode": True}

    # -- capture ---------------------------------------------------------
    def screen_size(self, monitor: int = 1) -> tuple:
        self._record("screen_size", (monitor,))
        return (_W, _H)

    def list_monitors(self) -> list:
        self._record("list_monitors")
        return [{"id": 1, "name": "Monitor 1", "left": 0, "top": 0,
                 "width": _W, "height": _H, "is_primary": True}]

    def screenshot(self, monitor: int = 1, region=None):
        self._record("screenshot", (monitor, region))
        return self._frame()

    def screenshot_jpeg(self, monitor: int = 1, region=None,
                        quality: int = 80) -> bytes:
        self._record("screenshot_jpeg", (monitor, region, quality))
        import io
        buf = io.BytesIO()
        self._frame().save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def screenshot_scaled(self, monitor: int = 1, region=None,
                          scale: float = 1.0, grayscale: bool = False):
        self._record("screenshot_scaled", (monitor, region, scale, grayscale))
        img = self._frame()
        if grayscale:
            img = img.convert("L")
        return img

    def frame_diff(self, prev, cur) -> dict:
        self._record("frame_diff")
        return {"changed": False, "changed_pct": 0.0, "tiles": [], "bbox": None}

    def capture_window(self, hwnd: int, client_only: bool = False):
        self._record("capture_window", (hwnd, client_only))
        self._require(hwnd)
        return self._frame()

    # -- mouse -----------------------------------------------------------
    def mouse_move(self, x: int, y: int, duration: float = 0.15) -> None:
        self._record("mouse_move", (x, y), {"duration": duration})

    def mouse_click(self, x, y, button: str = "left", clicks: int = 1) -> None:
        self._record("mouse_click", (x, y), {"button": button, "clicks": clicks})

    def mouse_scroll(self, clicks: int, x=None, y=None) -> None:
        self._record("mouse_scroll", (clicks, x, y))

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   duration: float = 0.3, button: str = "left") -> None:
        self._record("mouse_drag", (x1, y1, x2, y2),
                     {"duration": duration, "button": button})

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        self._record("mouse_move_relative", (dx, dy))

    def mouse_down(self, button: str = "left") -> None:
        self._record("mouse_down", (button,))
        self._held_buttons.add(button)

    def mouse_up(self, button: str = "left") -> None:
        self._record("mouse_up", (button,))
        self._held_buttons.discard(button)

    # -- keyboard --------------------------------------------------------
    def assert_allowed(self, keys) -> None:
        assert_forbidden(keys)

    def key_press(self, key: str) -> None:
        self._record("key_press", (key,))

    def key_down(self, key: str) -> None:
        self._record("key_down", (key,))
        self._held_keys.add(key.lower())

    def key_up(self, key: str) -> None:
        self._record("key_up", (key,))
        self._held_keys.discard(key.lower())

    def key_hotkey(self, *keys: str) -> None:
        self.assert_allowed(list(keys))
        self._record("key_hotkey", keys)

    def type_text(self, text: str, interval: float = 0.03) -> None:
        self._record("type_text", (text,), {"interval": interval})

    def held_state(self) -> dict:
        return {"keys": sorted(self._held_keys),
                "buttons": sorted(self._held_buttons)}

    def release_all(self) -> dict:
        self._record("release_all")
        released = ([k for k in sorted(self._held_keys)]
                    + [f"mouse:{b}" for b in sorted(self._held_buttons)])
        self._held_keys.clear()
        self._held_buttons.clear()
        return {"ok": True, "released": released}

    # -- windows ---------------------------------------------------------
    def list_windows(self) -> list:
        self._record("list_windows")
        return self._live()

    def get_focused_window(self):
        live = self._live()
        return next((w for w in live if w["focused"]), None)

    def focus_window(self, hwnd: int) -> None:
        self._record("focus_window", (hwnd,))
        self._require(hwnd)

    def close_window(self, hwnd: int, expect_title=None,
                     expect_process=None) -> dict:
        self._record("close_window", (hwnd, expect_title, expect_process))
        win = next((w for w in _FAKE_WINDOWS if w["hwnd"] == hwnd), None)
        if win is None or hwnd in self._closed:
            return {"ok": False, "error": f"window {hwnd} not found"}
        if expect_title and expect_title.lower() not in win["title"].lower():
            return {"ok": False, "error": f"title mismatch: {win['title']}"}
        if expect_process and win["process"] != expect_process:
            return {"ok": False, "error": "process mismatch"}
        self._closed.add(hwnd)
        return {"ok": True, "closed": True, "title": win["title"], "note": ""}

    def kill_process(self, pid: int) -> dict:
        self._record("kill_process", (pid,))
        return {"ok": True, "output": f"fake kill {pid}"}

    def process_name(self, pid: int) -> "str | None":
        return "fakeproc.exe" if pid and pid > 0 else None

    def probe_input_mode(self, hwnd: int) -> str:
        self._record("probe_input_mode", (hwnd,))
        return "postmessage"

    def maximize_window(self, hwnd: int) -> None:
        self._record("maximize_window", (hwnd,))
        self._require(hwnd)

    def set_topmost(self, hwnd: int, topmost: bool) -> None:
        self._record("set_topmost", (hwnd, topmost))
        self._require(hwnd)

    def window_type_text(self, hwnd: int, text: str) -> dict:
        self._record("window_type_text", (hwnd, text))
        return {"ok": True, "chars": len(text)}

    def window_key(self, hwnd: int, key: str) -> dict:
        self._record("window_key", (hwnd, key))
        return {"ok": True, "vk": 0}

    def window_hotkey(self, hwnd: int, keys) -> dict:
        self._record("window_hotkey", (hwnd, tuple(keys)))
        return {"ok": True, "vks": []}

    def window_click(self, hwnd: int, x: int, y: int,
                     button: str = "left", clicks: int = 1) -> dict:
        self._record("window_click", (hwnd, x, y),
                     {"button": button, "clicks": clicks})
        return {"ok": True, "x": x, "y": y, "button": button, "clicks": clicks}

    def window_scroll(self, hwnd: int, clicks: int) -> dict:
        self._record("window_scroll", (hwnd, clicks))
        return {"ok": True, "clicks": clicks}

    def window_drag(self, hwnd: int, x1: int, y1: int, x2: int, y2: int,
                    button: str = "left") -> dict:
        self._record("window_drag", (hwnd, x1, y1, x2, y2), {"button": button})
        return {"ok": True, "from": [x1, y1], "to": [x2, y2]}

    def list_children(self, hwnd: int) -> list:
        self._record("list_children", (hwnd,))
        return []

    def pick_input_child(self, hwnd: int) -> "int | None":
        self._record("pick_input_child", (hwnd,))
        return None

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple:
        self._record("client_to_screen", (hwnd, x, y))
        win = self._require(hwnd)
        return (win["rect"][0] + x, win["rect"][1] + y)

    # -- game mode -------------------------------------------------------
    def game_start(self, sensitivity: int = 12) -> dict:
        self._record("game_start", (sensitivity,))
        self._game = True
        return {"ok": True, "center": [_W // 2, _H // 2],
                "sensitivity": sensitivity, "note": "fake game mode"}

    def game_move(self, dx: int, dy: int, sensitivity: int = 12) -> dict:
        self._record("game_move", (dx, dy, sensitivity))
        return {"ok": True}

    def game_stop(self) -> dict:
        self._record("game_stop")
        self._game = False
        return {"ok": True, "released": []}

    def game_active(self) -> bool:
        return self._game
