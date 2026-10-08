"""Virtual uinput device for Linux game mode (ROADMAP Phase 5).

Game mode injects relative pointer motion and key/button events through
``/dev/uinput`` via python-evdev.  The name -> code mappings are pure data
mirrored from the stable Linux ``input-event-codes.h`` ABI, so importing
this module (and using the mapping helpers) never requires the library;
``evdev`` itself is imported lazily inside ``UInputDevice.__init__``.
"""
from __future__ import annotations

from core.errors import ApiError, permission_required

_REMEDIATION = (
    "add your user to the input group (sudo usermod -aG input $USER, "
    'then re-login) or install a udev rule: '
    'KERNEL=="uinput", MODE="0660", GROUP="input"'
)

# Kernel ABI values from include/uapi/linux/input-event-codes.h (frozen;
# the same integers evdev.ecodes exposes).
_KEY_CODES: dict = {
    # named aliases
    "ctrl": 29, "alt": 56, "shift": 42, "win": 125, "super": 125,
    "enter": 28, "esc": 1, "tab": 15, "space": 57, "backspace": 14,
    "delete": 111, "pageup": 104, "pagedown": 109,
    "escape": 1, "home": 102, "end": 107,
    # arrows
    "up": 103, "down": 108, "left": 105, "right": 106,
    # letters (KEY_A..KEY_Z)
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34,
    "h": 35, "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49,
    "o": 24, "p": 25, "q": 16, "r": 19, "s": 31, "t": 20, "u": 22,
    "v": 47, "w": 17, "x": 45, "y": 21, "z": 44,
    # digits (KEY_1..KEY_9, KEY_0)
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11,
    # function keys (KEY_F1..KEY_F12)
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}

_BUTTON_CODES: dict = {
    "left": 272,      # BTN_LEFT
    "right": 273,     # BTN_RIGHT
    "middle": 274,    # BTN_MIDDLE
}


def key_to_code(key: str) -> int:
    """Map an API key name (``"ctrl"``, ``"w"``, ``"f4"``) to a KEY_* code."""
    try:
        return _KEY_CODES[key.lower()]
    except KeyError:
        raise KeyError(f"unknown key: {key!r}") from None


def button_to_code(button: str) -> int:
    """Map an API button name (``"left"``/``"right"``/``"middle"``) to BTN_*."""
    try:
        return _BUTTON_CODES[button.lower()]
    except KeyError:
        raise KeyError(f"unknown button: {button!r}") from None


def uinput_permission_error(action: str) -> ApiError:
    """PERMISSION_REQUIRED envelope for a failed ``/dev/uinput`` open."""
    return permission_required(action, _REMEDIATION, platform="linux")


class UInputDevice:
    """Virtual pointer + keyboard on ``/dev/uinput`` for game mode."""

    def __init__(self) -> None:
        import evdev  # lazy: keeps this module importable without evdev

        self._ecodes = evdev.ecodes
        key_codes = sorted(set(_KEY_CODES.values()) | set(_BUTTON_CODES.values()))
        try:
            self._ui = evdev.UInput(events={
                evdev.ecodes.EV_KEY: key_codes,
                evdev.ecodes.EV_REL: [evdev.ecodes.REL_X, evdev.ecodes.REL_Y],
            }, name="screen-control")
        except OSError as exc:
            raise uinput_permission_error("game_start") from exc

    def move_rel(self, dx: int, dy: int) -> None:
        ec = self._ecodes
        self._ui.write(ec.EV_REL, ec.REL_X, int(dx))
        self._ui.write(ec.EV_REL, ec.REL_Y, int(dy))
        self._ui.syn()

    def key_down(self, key: str) -> None:
        self._key_event(key_to_code(key), 1)

    def key_up(self, key: str) -> None:
        self._key_event(key_to_code(key), 0)

    def button_down(self, button: str) -> None:
        self._key_event(button_to_code(button), 1)

    def button_up(self, button: str) -> None:
        self._key_event(button_to_code(button), 0)

    def close(self) -> None:
        if self._ui is not None:
            self._ui.close()
            self._ui = None

    def _key_event(self, code: int, value: int) -> None:
        self._ui.write(self._ecodes.EV_KEY, code, value)
        self._ui.syn()
