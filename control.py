"""
screen-control core: screen capture (mss) + mouse/keyboard control.

Cross-platform support:
  - Windows: Win32 API (SendInput, PostMessage, etc.)
  - macOS/Linux: pyautogui fallback (limited window management)

All Unicode text (including Turkish characters: ğ ü ş ı ö ç İ)
is sent via SendInput + KEYEVENTF_UNICODE on Windows, or pyautogui on other platforms.

Game mode: relative mouse movement (MOUSE_MOVE_RELATIVE) + held key/button tracking.

SECURITY:
- pyautogui FAILSAFE is enabled — move the cursor to the top-left corner to
  abort all commands instantly.
- Window closing is NEVER a blind Alt+F4: the target window receives WM_CLOSE
  only after focus verification passes (Windows only).
- Deadly key combos are blocked at the API level: Win key, Ctrl+Alt+Del, and
  ONLY Alt+F4 (other Alt combos — Alt+Tab, etc. — are legitimate and allowed).
- Held-key tracking: every key_down / mouse_down is recorded; release_all()
  releases everything on demand, and a watchdog releases after 30 s of
  server-side inactivity (stuck-key catastrophe becomes impossible).
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

pyautogui.FAILSAFE = True   # cursor to top-left corner aborts all commands
pyautogui.PAUSE = 0.05      # short inter-command delay


# ---------------------------------------------------------------------------
# Compatibility layer: the implementation moved to backends/windows.py.
# Every public name is re-exported so existing imports (server routes,
# test-game.py, sdk/) keep working unchanged.
# ---------------------------------------------------------------------------
from backends.windows import *                                    # noqa: F401,F403
from backends.windows import (                                    # noqa: F401
    IS_WINDOWS, FORBIDDEN_KEYS, FORBIDDEN_HOTKEYS,
    _assert_allowed, _game_active, _set_topmost, _user32,
)
