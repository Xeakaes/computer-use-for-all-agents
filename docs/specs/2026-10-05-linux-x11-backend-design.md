# Linux X11 Backend — Design Spec

Date: 2026-10-05
Status: approved (design reviewed in chat; implementation plan follows)
Scope: ROADMAP Phase 5 — real, tested Linux support for `computer-use-for-all-agents`

## 1. Goal

Replace the fail-closed `backends/linux.py` stub with a real, tested X11 backend
so the existing HTTP API (and every agent that talks to it) works unchanged on
Linux. Windows remains the reference backend and must not regress.

Target machine (verification environment):

- Linux Mint, Cinnamon, X11 session (`XDG_SESSION_TYPE=x11`, `DISPLAY=:0`)
- Single 1920x1080 display, scaling factor 1.0
- MSI Cyborg 15 (Intel iGPU + NVIDIA RTX 4060) — GPU/driver specifics are out
  of scope; the backend only uses X11 + mss capture
- Secure Boot ON, kernel lockdown (integrity) active — must NOT be bypassed or
  disabled. `/dev/uinput` works under lockdown; no lockdown changes are made.

## 2. Constraints

- API and agent side (`server.py` routes, `mcp_server.py`, `sdk/`) unchanged.
  Only the `PlatformBackend` implementation behind them changes.
- Wayland: fail loudly and clearly (`UNSUPPORTED_DISPLAY_SERVER`), never
  silently misbehave. Wayland support is out of scope (ROADMAP Phase 6).
- No system settings changed without explicit user announcement; every
  sudo-requiring command is announced before execution.
- Windows regression risk minimized: `backends/windows.py` gets exactly one
  import-safety guard (no-op on Windows); shared-file edits are
  platform-neutral.

## 3. Architecture

```
server.py ──> core/backends.py (PlatformBackend ABC, create_backend)
                     │
      ┌──────────────┼──────────────────┐
backends/windows.py  backends/linux.py  backends/fake.py   (macos: stub)
   (reference)       ├─ xdotool/wmctrl subprocess helpers
                     ├─ backends/imageops.py   (PIL helpers, shared logic)
                     └─ backends/uinput.py     (python-evdev virtual device)
```

- `backends/linux.py` — `LinuxBackend(PlatformBackend)`: X11 detection,
  subprocess helpers, window/input/game-mode logic. Roughly mirrors
  `backends/windows.py` structure (module functions + thin class binding).
- `backends/imageops.py` (new) — `frame_diff`, `screenshot_scaled`,
  `screenshot_jpeg` implementations copied from the Windows reference so both
  backends behave identically. `windows.py` is NOT refactored to import it
  (zero-risk for Windows).
- `backends/uinput.py` (new) — `UInputDevice`: opens `/dev/uinput` via
  python-evdev, exposes `move_rel(dx, dy)`, `key_down(key)`, `key_up(key)`,
  `button_down/up`, `close()`. Lazy import of `evdev`; construction failure
  surfaces as `ApiError(PERMISSION_REQUIRED)` with remediation text.

Backend selection is unchanged: `sys.platform` → `linux` on non-win32/darwin.

## 4. Method contracts (identical to the Windows reference)

| Group | Contract |
|---|---|
| `list_windows()` | `[{hwnd:int, title:str, process:str, pid:int, focused:bool, rect:[l,t,r,b]}]`; `hwnd` = X11 window id |
| `get_focused_window()` | that dict or `None` |
| `focus_window(hwnd)` | `None` or `RuntimeError` (server maps to 409) |
| `close_window(...)` | dict, never raises on mismatch; `ok:false` → server 409/404 |
| `kill_process(pid)` | `{"ok": bool, "output": str}` |
| `process_name(pid)` | `/proc/<pid>/comm` or `None` (server default-denies with 403) |
| `probe_input_mode(hwnd)` | `"focused"` (window exists) / `"invalid"` (gone) |
| `held_state()` / `release_all()` | fresh dicts `{"keys":[], "buttons":[]}` / `{"ok":True,"released":[]}` |
| `screenshot*` | PIL Image / JPEG bytes |
| `frame_diff` | `{"changed","bbox","changed_pct","tiles"}` (8x6 grid) |
| `game_start/move/stop` | `{"ok","center","sensitivity","note"}` / `{"ok"}` / `{"ok","released"}` |
| `get_capabilities()` | fresh dict, `platform:"linux"`, values in `True/False/"optional"/"strong"/None` |
| `assert_allowed(keys)` | `backends.forbidden.assert_forbidden` (unchanged policy) |

## 5. Implementation details

### 5.1 Input (absolute) — xdotool
- `mouse_move`: `xdotool mousemove --sync x y` (duration honored via stepped
  interpolation, ≤20 steps); `mouse_click/scroll/drag/down/up` via
  `xdotool click/mousedown/mouseup` (buttons 1/2/3, scroll 4/5 with sign
  mapping).
- `key_press/key_down/key_up`: `xdotool key/keydown/keyup`; `key_hotkey` → one
  `xdotool key ctrl+...` call after `assert_allowed`.
- `type_text` → single `xdotool type --delay <ms> -- <text>` call.
- Key-name mapping table (API name → X keysym): `enter→Return`,
  `esc→Escape`, arrows, `f1..f12`, `ctrl/alt/shift/super`, unknown names pass
  through as keysyms. Mapping table is a pure dict, unit-tested.
- Held tracking: module-level sets + lock, same semantics as Windows
  (`key_down` records, `key_up` discards, `release_all` releases everything).

### 5.2 Windows
- `list_windows`: `wmctrl -lGpx` (id, desktop, geometry, pid, class, title)
  + `/proc/<pid>/comm` for process names + `xdotool getactivewindow` for the
  focused flag. Empty titles filtered out (Windows parity).
- `focus_window`: `xdotool windowactivate --sync <id>`, verify via
  `getactivewindow`; mismatch → `RuntimeError`.
- `close_window`: title/process expectation checks (same messages as
  Windows) → `wmctrl -ic` → 1s poll → `{"ok","closed","title","note"}`.
- `maximize_window` / `set_topmost`: `wmctrl -b add/remove,...`.
- `list_children`: parse `xwininfo -id <id> -tree`. `pick_input_child`:
  class-name heuristic. `client_to_screen`: `xdotool getwindowgeometry
  --shell` origin + client offset.
- `window_*` background methods: **focus-first** (focus, then global input;
  XSendEvent is unreliable). Return dicts match the Windows shapes.
  Documented as a known limitation (focus stealing).

### 5.3 Capture
- `mss` for `screen_size/list_monitors/screenshot*` (parity with Windows).
- `capture_window`: ImageMagick `import -window <id>` when available,
  else mss region from window geometry (occluded windows capture what is on
  top — documented limitation). Failures raise `ValueError/RuntimeError`
  (server maps to 409), matching the Windows behavior.

### 5.4 Game mode — uinput
- `game_start`: `release_all()` → open `UInputDevice` (REL_X/REL_Y,
  BTN_LEFT/RIGHT/MIDDLE, common KEY_*) → set active flag → return screen
  center + sensitivity + note ("no cursor clip on X11; the game must capture
  the pointer").
- `game_move(dx, dy, sens)`: emit `REL_X/REL_Y = dx*sens, dy*sens` + SYN.
- Keys while game mode is active route through the uinput keyboard
  (`key_down/up/press`); outside game mode through xdotool. Held-key records
  remember their device so `release_all` releases on the right one.
- `game_stop`: release all uinput-held keys/buttons, close device, clear flag.
- `game_active`: internal flag only (cheap, non-raising — watchdog contract).
- No `/dev/uinput` access → `ApiError(PERMISSION_REQUIRED)` with remediation:
  `sudo usermod -aG input $USER` (re-login) or udev rule
  `KERNEL=="uinput", MODE="0660", GROUP="input"`.

### 5.5 Wayland / display detection
- Module init detects Wayland: `XDG_SESSION_TYPE == "wayland"` or
  (`WAYLAND_DISPLAY` set and session type is not `x11`).
- On Wayland: backend construction raises
  `ApiError("UNSUPPORTED_DISPLAY_SERVER", status=501)` with a clear message
  and remediation ("start Cinnamon in an X11 session").
- `server.py`: wrap `_backend = get_backend()` in try/except → print the
  envelope message to stderr and `sys.exit(2)` instead of a traceback.
- No `DISPLAY` at all (CI): construction succeeds; X-dependent actions raise
  `BACKEND_UNAVAILABLE` (501); held/release/game getters return safe defaults
  (keeps watchdog and unit tests functional).

### 5.6 Capabilities
```python
{"platform": "linux", "screen_capture": True, "ocr": "optional",
 "mouse_control": True, "keyboard_control": True,
 "window_enumeration": True, "background_input": False,
 "virtual_desktops": False, "game_mode": True}
```
(`game_mode` downgraded to `False` only if the uinput device cannot be
opened at capability time without raising.)

## 6. Files touched

| File | Change |
|---|---|
| `backends/linux.py` | stub → real implementation (~600 lines) |
| `backends/imageops.py` | new |
| `backends/uinput.py` | new |
| `backends/windows.py` | guard `BITMAPINFO*` classes under `IS_WINDOWS` (import safety; no-op on Windows) |
| `server.py` | backend-creation try/except → clean startup error |
| `requirements.txt` | `pyvda; sys_platform=="win32"`, add `python-evdev; sys_platform=="linux"` |
| `tests/unit/test_stub_backends.py` | scope stub assertions to macOS only |
| `tests/unit/test_linux_backend.py` | new unit tests (CI-safe, no display needed) |
| `test-linux.py` | new live test script (the 7 acceptance items) |
| `install-linux.sh`, `start-server.sh` | new Linux setup/launch scripts |
| `README.md` | Linux section (requirements, X11-only, uinput perms, limits) |
| `ROADMAP.md` | tick Phase 5 checkboxes |

## 7. Error handling

- All refusals use `ApiError` codes already defined in `core/errors.py`:
  `UNSUPPORTED_DISPLAY_SERVER` (Wayland), `BACKEND_UNAVAILABLE` (no
  DISPLAY / missing tool), `PERMISSION_REQUIRED` (uinput), `INPUT_BLOCKED`
  (forbidden keys), `WINDOW_NOT_FOUND` / `FOCUS_MISMATCH` via server logic.
- Missing `xdotool`/`wmctrl` at call time → `BACKEND_UNAVAILABLE` with
  `remediation: "sudo apt install xdotool wmctrl"`.
- `held_state`/`release_all`/`game_active` never raise (watchdog contract).

## 8. Testing

Unit (CI-safe, run on all matrix OSes with `SCREEN_CONTROL_BACKEND` default):
- Wayland detection raises the right envelope (env vars patched)
- No-DISPLAY fail-closed: every action raises `BACKEND_UNAVAILABLE` 501
- held/release/game getters return the exact shapes
- key-name → keysym and → KEY_* mapping tables
- `wmctrl`/`xwininfo` parser fixtures (pure string → dict)
- forbidden-key policy unchanged
- `test_stub_backends.py` keeps running for macOS

Live (`test-linux.py`, this machine only, each item prints PASS/FAIL):
1. desktop mouse move + click (verify via `xdotool getmouselocation` + `xev`)
2. keyboard typing + hotkey (verify via `xev` key events)
3. screenshot via API (file inspected)
4. OCR of a known-text window via `/api/ocr`
5. window list + focus change (verify via `getactivewindow`)
6. game mode: uinput relative look + key hold (verified on an `xev` target;
   no native Linux game is installed on this machine — reported honestly)
7. CS2 offline input check — **skipped: not installed on Linux** (Windows
   Steam library only)

Regression:
- `python -m unittest discover -s tests/unit` must be ≥ baseline
  (25 pass / 4 skip / 1 pre-existing flask error → flask installed, so the
  error must disappear)
- `git diff --ignore-cr-at-eol` review so the CRLF working-tree noise does not
  hide real changes
- Windows: no behavioral change (guard is a no-op on win32; CI matrix runs the
  fake-backend unit suite on windows-latest when pushed)

## 9. Out of scope

- Wayland support (Phase 6)
- macOS backend
- Virtual desktop/workspace API on Linux (`/api/desktops` stays 503)
- Fractional scaling validation (display runs at 1.0; documented as untested)
- Committing/pushing (user must ask explicitly)

## Errata

Entries dated 2026-10-08 (post-implementation); the spec body above is left
as reviewed.

- **§5.2 `client_to_screen`** — implemented with `xwininfo` "Absolute
  upper-left", not `xdotool getwindowgeometry --shell`: on the tested system
  xdotool returns abs+parent-relative coordinates (same bug as `wmctrl -G`);
  `xwininfo` is Xlib-verified correct.
- **§5.2 `list_windows` geometry** — `wmctrl -lGpx` X/Y are unusable for the
  same reason, so `rect` now comes from `xwininfo -root -tree` absolute
  bounds (frame/parent bounds when reparented = §4 `GetWindowRect` parity);
  wmctrl still supplies id/desktop/pid/class/title.
- **§5.4 `game_start` note** — the brief/plan string ("cursor locked to
  center; use /api/game/move for camera look; X11 cannot clip the cursor —
  the game must capture the pointer") is authoritative; the §5.4 quoted
  phrase was a paraphrase.
- **§5.1 key names** — `super`/`meta` are policy-normalized to `win` for the
  forbidden-key checks (on Linux, `super`/`meta` are unsendable exactly like
  `win` is on Windows; on Windows the alias normalization does not apply —
  known asymmetry); multi-part names like `alt+f4` are rejected with
  `ValueError` (→ HTTP 400) in the single-key functions — chord grammar
  belongs to the hotkey action.
