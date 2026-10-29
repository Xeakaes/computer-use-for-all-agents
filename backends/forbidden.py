"""Forbidden-key policy shared by every backend (SC-02 parity).

Extracted from control.py so the ban list stays identical no matter which
backend the server selects. Performs no OS calls.

VULN-004 (strix audit): policy matches on CANONICAL key identity. Backends
speak different dialects — X11 keysyms (Super_L, Alt_L, KP_Delete), raw
Windows VK codes ("91", "18"), friendly aliases (win, cmd) — and every one
of those dialects used to be a bypass. canonical_policy_keys() folds them
into the friendly names the policy is written against.
"""
FORBIDDEN_KEYS = {"win", "leftwin", "rightwin", "cmd", "delete"}
FORBIDDEN_HOTKEYS = {frozenset(("alt", "f4")), frozenset(("win", "l")),
                     frozenset(("ctrl", "alt", "delete")), frozenset(("win", "d")),
                     frozenset(("win", "e")), frozenset(("win", "m"))}

# Every alias a backend can hand us for a policy-relevant key, folded to
# the canonical name used above. Keysym suffixes (_L/_R), XKB levels and
# vendor spellings all land here — unknown names pass through untouched.
_KEY_ALIASES = {
    # Super / Meta / Windows key
    "super": "win", "super_l": "win", "super_r": "win",
    "meta": "win", "meta_l": "win", "meta_r": "win",
    "leftwin": "win", "rightwin": "win",
    "cmd": "win", "command": "win", "lwin": "win", "rwin": "win",
    # Alt
    "alt": "alt", "alt_l": "alt", "alt_r": "alt",
    "option": "alt", "iso_level3_shift": "alt", "level3_shift": "alt",
    # Ctrl
    "ctrl": "ctrl", "control": "ctrl",
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "control_l": "ctrl", "control_r": "ctrl",
    # Delete
    "delete": "delete", "del": "delete", "kp_delete": "delete",
}

# Windows virtual-key codes folded to canonical policy names. Only codes
# that matter to the policy (the key itself or an ingredient of a banned
# chord) are listed; harmless VK digits pass through unchanged.
_VK_POLICY_NAMES = {
    0x5B: "win",     # VK_LWIN
    0x5C: "win",     # VK_RWIN
    0x2E: "delete",  # VK_DELETE
    0x12: "alt",     # VK_MENU (chord ingredient: 18+115 = Alt+F4)
    0x11: "ctrl",    # VK_CONTROL
    0x10: "shift",   # VK_SHIFT
    0x73: "f4",      # VK_F4 (chord ingredient)
}


def canonical_key(name) -> str:
    """Fold one key name to its canonical policy identity."""
    n = str(name).strip().lower()
    return _KEY_ALIASES.get(n, n)


def canonical_policy_keys(keys, vk_lookup=None) -> list:
    """Fold a key list to canonical policy names.

    vk_lookup (optional): callable mapping a raw name to a Windows VK code
    (int) or None. When it resolves a digit string to a policy-relevant VK
    code, the digit is folded to that code's canonical name — closing the
    raw-VK bypass ("91" = VK_LWIN = the Win key).
    """
    out = []
    for key in keys:
        n = str(key).strip().lower()
        if n.isdigit() and vk_lookup is not None:
            vk = vk_lookup(n)
            if vk is not None:
                n = _VK_POLICY_NAMES.get(vk, n)
        out.append(_KEY_ALIASES.get(n, n))
    return out


def assert_forbidden(keys) -> None:
    """Raise PermissionError if the requested combo is banned. No OS calls."""
    norm = frozenset(canonical_policy_keys(keys))
    if norm in FORBIDDEN_HOTKEYS:
        raise PermissionError(
            f"Shortcut {sorted(norm)} is blocked by this server. "
            f"Use /api/window (WM_CLOSE) to close windows instead.")
    if norm == frozenset(("ctrl", "shift", "esc")):
        return  # Task Manager opening is allowed
    for k in norm:
        if k in FORBIDDEN_KEYS:
            raise PermissionError(f"Key '{k}' is blocked by this server.")
