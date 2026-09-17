"""Forbidden-key policy shared by every backend (SC-02 parity).

Extracted from control.py so the ban list stays identical no matter which
backend the server selects. Performs no OS calls.
"""
FORBIDDEN_KEYS = {"win", "leftwin", "rightwin", "cmd", "delete"}
FORBIDDEN_HOTKEYS = {frozenset(("alt", "f4")), frozenset(("win", "l")),
                     frozenset(("ctrl", "alt", "delete")), frozenset(("win", "d")),
                     frozenset(("win", "e")), frozenset(("win", "m"))}


def assert_forbidden(keys) -> None:
    """Raise PermissionError if the requested combo is banned. No OS calls."""
    norm = frozenset(k.lower() for k in keys)
    if norm in FORBIDDEN_HOTKEYS:
        raise PermissionError(
            f"Shortcut {sorted(norm)} is blocked by this server. "
            f"Use /api/window (WM_CLOSE) to close windows instead.")
    if norm == frozenset(("ctrl", "shift", "esc")):
        return  # Task Manager opening is allowed
    for k in keys:
        if k.lower() in FORBIDDEN_KEYS:
            raise PermissionError(f"Key '{k}' is blocked by this server.")
