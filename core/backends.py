"""PlatformBackend: the single OS-facing interface for screen-control.

server.py talks ONLY to this interface; OS-specific code lives in
backends/<platform>.py. Backend selection stays lazy and overridable so
tests can swap implementations (SCREEN_CONTROL_BACKEND env var or
set_backend()).
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod


class PlatformBackend(ABC):
    """Abstract perception + actuation surface for one operating system."""

    # -- capabilities ----------------------------------------------------
    @abstractmethod
    def get_capabilities(self) -> dict: ...

    # -- capture ---------------------------------------------------------
    @abstractmethod
    def screen_size(self, monitor: int = 1) -> tuple: ...

    @abstractmethod
    def list_monitors(self) -> list: ...

    @abstractmethod
    def screenshot(self, monitor: int = 1, region=None): ...

    @abstractmethod
    def screenshot_jpeg(self, monitor: int = 1, region=None,
                        quality: int = 80) -> bytes: ...

    @abstractmethod
    def screenshot_scaled(self, monitor: int = 1, region=None,
                          scale: float = 1.0, grayscale: bool = False): ...

    @abstractmethod
    def frame_diff(self, prev, cur) -> dict: ...

    @abstractmethod
    def capture_window(self, hwnd: int, client_only: bool = False): ...

    # -- mouse -----------------------------------------------------------
    @abstractmethod
    def mouse_move(self, x: int, y: int, duration: float = 0.15) -> None: ...

    @abstractmethod
    def mouse_click(self, x, y, button: str = "left", clicks: int = 1) -> None: ...

    @abstractmethod
    def mouse_scroll(self, clicks: int, x=None, y=None) -> None: ...

    @abstractmethod
    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   duration: float = 0.3, button: str = "left") -> None: ...

    @abstractmethod
    def mouse_move_relative(self, dx: int, dy: int) -> None: ...

    @abstractmethod
    def mouse_down(self, button: str = "left") -> None: ...

    @abstractmethod
    def mouse_up(self, button: str = "left") -> None: ...

    # -- keyboard --------------------------------------------------------
    @abstractmethod
    def assert_allowed(self, keys) -> None: ...

    @abstractmethod
    def key_press(self, key: str) -> None: ...

    @abstractmethod
    def key_down(self, key: str) -> None: ...

    @abstractmethod
    def key_up(self, key: str) -> None: ...

    @abstractmethod
    def key_hotkey(self, *keys: str) -> None: ...

    @abstractmethod
    def type_text(self, text: str, interval: float = 0.03) -> None: ...

    @abstractmethod
    def held_state(self) -> dict: ...

    @abstractmethod
    def release_all(self) -> dict: ...

    # -- windows ---------------------------------------------------------
    @abstractmethod
    def list_windows(self) -> list: ...

    @abstractmethod
    def get_focused_window(self): ...

    @abstractmethod
    def focus_window(self, hwnd: int) -> None: ...

    @abstractmethod
    def close_window(self, hwnd: int, expect_title=None,
                     expect_process=None) -> dict: ...

    @abstractmethod
    def kill_process(self, pid: int) -> dict: ...

    @abstractmethod
    def process_name(self, pid: int) -> "str | None": ...

    @abstractmethod
    def probe_input_mode(self, hwnd: int) -> str: ...

    @abstractmethod
    def maximize_window(self, hwnd: int) -> None: ...

    @abstractmethod
    def set_topmost(self, hwnd: int, topmost: bool) -> None: ...

    @abstractmethod
    def window_type_text(self, hwnd: int, text: str) -> dict: ...

    @abstractmethod
    def window_key(self, hwnd: int, key: str) -> dict: ...

    @abstractmethod
    def window_hotkey(self, hwnd: int, keys) -> dict: ...

    @abstractmethod
    def window_click(self, hwnd: int, x: int, y: int,
                     button: str = "left", clicks: int = 1) -> dict: ...

    @abstractmethod
    def window_scroll(self, hwnd: int, clicks: int) -> dict: ...

    @abstractmethod
    def window_drag(self, hwnd: int, x1: int, y1: int, x2: int, y2: int,
                    button: str = "left") -> dict: ...

    @abstractmethod
    def list_children(self, hwnd: int) -> list: ...

    @abstractmethod
    def pick_input_child(self, hwnd: int) -> "int | None": ...

    @abstractmethod
    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple: ...

    # -- game mode -------------------------------------------------------
    @abstractmethod
    def game_start(self, sensitivity: int = 12) -> dict: ...

    @abstractmethod
    def game_move(self, dx: int, dy: int, sensitivity: int = 12) -> dict: ...

    @abstractmethod
    def game_stop(self) -> dict: ...

    @abstractmethod
    def game_active(self) -> bool: ...


def create_backend(backend_name: "str | None" = None) -> PlatformBackend:
    """Instantiate a backend by name (lazy import of only that backend).

    Resolution order: explicit argument -> SCREEN_CONTROL_BACKEND env var ->
    sys.platform mapping (win32/darwin/else-linux). Unknown names raise
    ValueError BEFORE any module import, so a typo cannot degrade into the
    wrong platform's backend.
    """
    name = (backend_name
            or os.environ.get("SCREEN_CONTROL_BACKEND")
            or {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux"))
    if name == "windows":
        from backends.windows import WindowsBackend as backend_cls
    elif name == "linux":
        from backends.linux import LinuxBackend as backend_cls
    elif name == "macos":
        from backends.macos import MacOSBackend as backend_cls
    elif name == "fake":
        # In-memory backend for CI / tests / safe experimentation.
        from backends.fake import FakeBackend as backend_cls
    else:
        raise ValueError(f"unknown backend: {name!r} "
                         f"(known: 'windows', 'linux', 'macos', 'fake')")
    return backend_cls()


_backend_singleton: "PlatformBackend | None" = None


def get_backend() -> PlatformBackend:
    """Process-wide backend singleton (created on first call)."""
    global _backend_singleton
    if _backend_singleton is None:
        _backend_singleton = create_backend()
    return _backend_singleton


def set_backend(backend: PlatformBackend) -> None:
    """Override the singleton (used by tests to install the FakeBackend)."""
    global _backend_singleton
    _backend_singleton = backend
