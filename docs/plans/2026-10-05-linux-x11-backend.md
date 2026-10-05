# Linux X11 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fail-closed `backends/linux.py` stub with a real, tested X11 backend so the unchanged HTTP API works on Linux Mint/Cinnamon X11, with Windows untouched.

**Architecture:** `server.py` keeps talking only to `PlatformBackend`. The new `backends/linux.py` drives X11 through `xdotool`/`wmctrl` subprocesses (absolute input, window management) + `mss` (capture) + `backends/uinput.py` (python-evdev virtual device for game mode). PIL helpers live in a new `backends/imageops.py` copied from the Windows reference; `backends/windows.py` gets exactly one import-safety guard.

**Tech Stack:** Python 3.12, Flask, mss, Pillow, numpy, python-evdev, xdotool, wmctrl, ImageMagick (`import`), RapidOCR (existing flow), unittest.

**Spec:** `docs/specs/2026-10-05-linux-x11-backend-design.md`

## Global Constraints

- API/agent side (`server.py` routes, `mcp_server.py`, `sdk/`) unchanged; only the backend layer changes.
- Wayland must raise `ApiError("UNSUPPORTED_DISPLAY_SERVER", status=501)` — never silently misbehave. Wayland support is out of scope.
- Windows must not regress: `backends/windows.py` gets only the `BITMAPINFO*` `IS_WINDOWS` guard (no-op on win32); no other Windows file is refactored.
- Every sudo command is announced to the user before execution. No system settings changed otherwise; Secure Boot/lockdown untouched.
- Commits are allowed **only on the local `linux-x11-backend` branch** (user-approved 2026-10-05); never push, never touch `main`.
- Repo test runner is `unittest`, not pytest: `.venv/bin/python -m unittest <module> -v`.
- `.venv` is created by `install-linux.sh`; all Python commands in this plan use `.venv/bin/python`.
- Line endings: new files LF; do not rewrite existing files wholesale (working tree is CRLF — preserve it, review real changes with `git diff --ignore-cr-at-eol`).
- Exact contract values (dict keys, error codes, statuses) come from the spec §4 — every task reproduces them verbatim.

## Review Focus

Failure modes the spec implies but no single task naturally proves; each line names its owning test.

1. **Watchdog safety** — `held_state()`/`game_active()` must never raise even if `xdotool` is missing/broken mid-run, or the watchdog disables itself silently → Task 6 test: `test_held_state_never_raises_without_xdotool` (PATH stripped).
2. **Focus guard false positives** — if `get_focused_window().hwnd` disagrees with the X11 active window, every `/api/key` with `expect_hwnd` returns 409 → Task 7 test: `test_focus_window_makes_it_active` (DISPLAY-gated).
3. **Coordinate origin error** — Cinnamon reparenting means client origin ≠ frame origin; a wrong `client_to_screen` makes `window_click` hit the title bar → Task 10 live item 5b clicks a known client coordinate and verifies the landing point with `xdotool getmouselocation`.
4. **Held-key leakage across devices** — a key pressed via xdotool before `game_start` must still be released by `release_all` when other keys are held on uinput → Task 9 test: `test_release_all_releases_both_sources` (both devices held).
5. **xdotool hang** — an XGrab'd client can block `xdotool --sync` forever → Task 6: subprocess runner with `timeout=5` + test `test_runner_raises_operation_timeout_on_hang`.

---

### Task 1: Dependency bootstrap (requirements, install script, venv, baseline)

**Files:**
- Modify: `requirements.txt`
- Create: `install-linux.sh`, `start-server.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `.venv` with all deps importable; commands `.venv/bin/python -m unittest ...` usable by every later task; `install-linux.sh` re-runnable idempotently.

- [ ] **Step 1: Update `requirements.txt`**
  - `pyvda>=0.4` → `pyvda>=0.4; sys_platform == "win32"`
  - add `python-evdev>=1.3; sys_platform == "linux"` under Core
  - add `numpy>=1.26` under Core (`frame_diff` imports numpy; today it arrives only via rapidocr)

- [ ] **Step 2: Write `install-linux.sh`**
  Checks (in order, each with a clear message): `XDG_SESSION_TYPE=wayland`/`WAYLAND_DISPLAY` → exit 1 with "requires X11"; `DISPLAY` unset → warn; then `sudo apt-get update && sudo apt-get install -y xdotool wmctrl imagemagick python3-venv python3-tk`; `python3 -m venv .venv`; `.venv/bin/pip install -r requirements.txt`; finally report `/dev/uinput` writability (`[ -w /dev/uinput ]` → "game mode: OK" or the two remediation commands from spec §5.4).

- [ ] **Step 3: Write `start-server.sh`**
  Mirrors `start-server.bat`: `cd "$(dirname "$0")"`, require `.venv` (else "Run ./install-linux.sh first"), `exec .venv/bin/python server.py "$@"`. Both scripts `chmod +x`.

- [ ] **Step 4: Announce sudo, then run the install**

Announce to the user, then run:
```bash
bash install-linux.sh
```
Expected: apt packages installed, `.venv` created, pip completes (rapidocr ≈ 200 MB download), uinput line reports OK (ACL already grants `xeakaes`).

- [ ] **Step 5: Verify baseline**
```bash
.venv/bin/python -c "import flask, mss, PIL, numpy, evdev, pyautogui; print('deps ok')"
.venv/bin/python -m unittest discover -s tests/unit -t . -v 2>&1 | tail -5
xdotool --version && wmctrl -v
```
Expected: `deps ok`; test run has **0 errors, 0 failures** (baseline was 1 flask error, now gone; 4 win32 skips remain).

---

### Task 2: `backends/windows.py` import guard

**Files:**
- Modify: `backends/windows.py:851-862` (`BITMAPINFOHEADER`, `BITMAPINFO` at module level)
- Test: `tests/unit/test_backend_import.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `import backends.windows` works on Linux (needed so the module is import-testable and `SCREEN_CONTROL_BACKEND=windows` fails at call time, not at import).

- [ ] **Step 1: Write the failing test** in `tests/unit/test_backend_import.py`
```python
def test_backends_windows_importable_on_this_platform(self):
    try:
        import backends.windows  # noqa: F401
    except NameError:
        self.fail("unguarded Windows-only symbol at import time")
    except ImportError as e:
        self.skipTest(f"optional platform dep missing: {e}")
```
(On this machine after Task 1 the import must fully succeed — no skip.)

- [ ] **Step 2: Run it, see it fail**
```bash
.venv/bin/python -m unittest tests.unit.test_backend_import -v
```
Expected: FAIL with `NameError: name 'ctypes' is not defined` (line ~851).

- [ ] **Step 3: Guard the two classes**
Move `class BITMAPINFOHEADER` and `class BITMAPINFO` under `if IS_WINDOWS:` (they are only used by the Windows `capture_window` path). No other edit in this file.

- [ ] **Step 4: Re-run, expect PASS**
```bash
.venv/bin/python -m unittest tests.unit.test_backend_import -v
```

---

### Task 3: `backends/imageops.py` — shared PIL helpers

**Files:**
- Create: `backends/imageops.py`
- Test: `tests/unit/test_imageops.py` (new)

**Interfaces:**
- Consumes: Pillow, numpy.
- Produces (exact signatures; Task 8 wires them into `LinuxBackend`):
  - `encode_jpeg(img: Image.Image, quality: int = 80) -> bytes`
  - `scale_image(img: Image.Image, scale: float = 1.0, grayscale: bool = False) -> Image.Image`
  - `frame_diff(prev: Image.Image, cur: Image.Image) -> dict`
  - constants `DIFF_GRID_COLS=8`, `DIFF_GRID_ROWS=6`, `DIFF_PIX_THRESHOLD=12`, `DIFF_TILE_MIN_PCT=1.0`
  - Logic copied verbatim from `backends/windows.py:240-304` (bodies are the spec's fixed contract — do not redesign).

- [ ] **Step 1: Write failing tests** in `tests/unit/test_imageops.py`
  - `test_encode_jpeg_returns_jpeg_bytes`: `encode_jpeg(solid_image, 70)` starts with `b"\xff\xd8"` and is non-empty.
  - `test_scale_image_shrinks_and_grayscales`: 100x100 image, `scale_image(img, 0.5, True)` → size (50,50), mode `"L"`; `scale_image(img, 1.0, False)` → size unchanged.
  - `test_frame_diff_identical_frames`: two equal 64x48 images → `changed is False`, `changed_pct == 0.0`, `tiles == []`.
  - `test_frame_diff_changed_tile_shape`: draw a white 10x10 square at (5,5) in frame 2 → `changed is True`, `bbox` is `[x0,y0,x1,y1]` list, every tile has keys `{"row","col","pct","center"}` with `0<=row<6`, `0<=col<8`.
  - `test_frame_diff_size_mismatch_resizes`: prev 64x48, cur 32x24 → no exception, returns the dict shape.

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: backends.imageops`)

- [ ] **Step 3: Implement** in `backends/imageops.py` — copy the four pieces from `backends/windows.py:240-304` verbatim, replacing the module-internal `screenshot()` call with the `img` parameter.

- [ ] **Step 4: Run, expect PASS**
```bash
.venv/bin/python -m unittest tests.unit.test_imageops -v
```

---

### Task 4: `backends/uinput.py` — virtual device for game mode

**Files:**
- Create: `backends/uinput.py`
- Test: `tests/unit/test_uinput.py` (new)

**Interfaces:**
- Consumes: `python-evdev` (`evdev.UInput`, `evdev.ecodes`), `/dev/uinput`, `core.errors.ApiError`.
- Produces (Task 9 relies on these exact names):
  - `key_to_code(key: str) -> int` — API name → `KEY_*` code; aliases: `ctrl→KEY_LEFTCTRL`, `alt→KEY_LEFTALT`, `shift→KEY_LEFTSHIFT`, `win/super→KEY_LEFTMETA`, `enter→KEY_ENTER`, `esc→KEY_ESC`, `tab→KEY_TAB`, `space→KEY_SPACE`, `backspace→KEY_BACKSPACE`, `delete→KEY_DELETE`, `pageup→KEY_PAGEUP`, `pagedown→KEY_PAGEDOWN`, `up/down/left/right→KEY_UP/...`, `f1..f12→KEY_F1..`; single letters/digits via `KEY_A..KEY_Z`/`KEY_1..`; unknown → `KeyError`
  - `button_to_code(button: str) -> int` — `left→BTN_LEFT`, `right→BTN_RIGHT`, `middle→BTN_MIDDLE`
  - `class UInputDevice`: `__init__(self)` opens `/dev/uinput` with `EV_KEY` (all common keys+buttons) + `EV_REL` (`REL_X`, `REL_Y`); methods `move_rel(self, dx: int, dy: int) -> None`, `key_down(self, key: str) -> None`, `key_up(self, key: str) -> None`, `button_down(self, button: str) -> None`, `button_up(self, button: str) -> None`, `close(self) -> None`
  - `uinput_permission_error(action: str) -> ApiError` — code `PERMISSION_REQUIRED`, status 403, remediation text exactly: `"add your user to the input group (sudo usermod -aG input $USER, then re-login) or install a udev rule: KERNEL==\"uinput\", MODE=\"0660\", GROUP=\"input\""`
  - Open failure inside `UInputDevice.__init__` raises that ApiError.

- [ ] **Step 1: Write failing tests** in `tests/unit/test_uinput.py`
  - `test_key_to_code_aliases`: `key_to_code("ctrl") == evdev.ecodes.KEY_LEFTCTRL`, `key_to_code("w") == KEY_W`, `key_to_code("f4") == KEY_F4`, `key_to_code("enter") == KEY_ENTER`.
  - `test_key_to_code_unknown_raises`: `assertRaises(KeyError)` on `"notakey"`.
  - `test_button_to_code`: left/right/middle map to BTN_*.
  - `test_permission_error_envelope`: `uinput_permission_error("game_start")` has `.code == "PERMISSION_REQUIRED"`, `.status == 403`, remediation mentions `input` group.
  - `test_uinput_device_roundtrip` — `@skipUnless(os.access("/dev/uinput", os.R_OK | os.W_OK))`: open, `move_rel(3, -2)`, `key_down("w")`, `key_up("w")`, `close()` without raising (events go nowhere harmful; device is closed on exit).

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`)

- [ ] **Step 3: Implement** `backends/uinput.py` per the Interfaces block; import `evdev` lazily inside `UInputDevice.__init__` so unit tests of the mapping functions run without the library.

- [ ] **Step 4: Run, expect PASS** (device test runs on this machine — ACL grants access; it skips in CI)
```bash
.venv/bin/python -m unittest tests.unit.test_uinput -v
```

---

### Task 5: `LinuxBackend` skeleton — display detection, Wayland, capabilities, clean server startup

**Files:**
- Rewrite: `backends/linux.py` (keep `LinuxBackend` name; every not-yet-implemented method keeps raising `BACKEND_UNAVAILABLE` 501 exactly as today)
- Modify: `server.py:38` (backend creation)
- Test: `tests/unit/test_linux_backend.py` (new)

**Interfaces:**
- Consumes: `core.errors.ApiError`, `backends.forbidden.assert_forbidden`.
- Produces (Tasks 6–8 replace the stub bodies):
  - `display_server() -> str` — `"wayland" | "x11" | "none"` (wayland if `XDG_SESSION_TYPE == "wayland"` or (`WAYLAND_DISPLAY` set and `XDG_SESSION_TYPE != "x11"`); x11 if `XDG_SESSION_TYPE == "x11"` or `DISPLAY` set; else none)
  - `LinuxBackend.__init__(self)` raises `ApiError("UNSUPPORTED_DISPLAY_SERVER", status=501, platform="linux", remediation="start your desktop session in X11 mode (e.g. 'Cinnamon on X11' at the login screen)")` when `display_server() == "wayland"`
  - `require_x(self, action: str) -> None` — raises `ApiError("BACKEND_UNAVAILABLE", status=501, platform="linux", action=action, remediation="X11 DISPLAY not available")` when `display_server() != "x11"`
  - `get_capabilities(self) -> dict` returning exactly spec §5.6's dict (`background_input: False`, `virtual_desktops: False`, `ocr: "optional"`)

- [ ] **Step 1: Write failing tests** in `tests/unit/test_linux_backend.py`
  - `test_capabilities_match_spec` — keys/values equal spec §5.6 dict.
  - `test_wayland_raises_unsupported_display_server` — with `mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"})`: `assertRaises(ApiError)` on `LinuxBackend()`; code `UNSUPPORTED_DISPLAY_SERVER`, status 501.
  - `test_x11_session_constructs` — env `XDG_SESSION_TYPE=x11`, `DISPLAY=:0` → constructs, capabilities OK.
  - `test_no_display_fails_closed` — env without `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_SESSION_TYPE`: constructs, and `mouse_move(1,1)`, `key_press("a")`, `list_windows()`, `screenshot()` each raise `ApiError` with code `BACKEND_UNAVAILABLE`, status 501.
  - `test_read_only_getters_safe_without_display` — `held_state() == {"keys": [], "buttons": []}`, `release_all() == {"ok": True, "released": []}`, `game_active() is False`, `probe_input_mode(1) == "invalid"`.
  - `test_forbidden_policy` — `assert_allowed(["a"])` returns None; `assert_allowed(["alt","f4"])` raises `PermissionError`.
  - `test_stub_backends_is_macos_only` — read `tests/unit/test_stub_backends.py` source and assert `"LinuxBackend"` no longer appears there (guards the re-scope).

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** — rewrite `backends/linux.py`: display detection + `__init__` guard + capabilities; all action methods call `self.require_x("<action>")` and then still raise the current `_unavailable()` (later tasks replace each group). Move `held_state/release_all/game_active/probe_input_mode` to the real safe-default implementations from Step 1. Then re-scope `tests/unit/test_stub_backends.py` to `MacOSBackend` only (its loop and docstring).

- [ ] **Step 4: Run the whole unit suite**
```bash
.venv/bin/python -m unittest discover -s tests/unit -t . 2>&1 | tail -4
```
Expected: 0 failures, 0 errors (win32-only skips only).

- [ ] **Step 5: Clean server startup error**
Wrap `_backend = get_backend()` at `server.py:38` in `try/except ApiError:` → print `exc.message` and `exc.remediation` to stderr → `raise SystemExit(2)`.

- [ ] **Step 6: Verify**
```bash
XDG_SESSION_TYPE=wayland WAYLAND_DISPLAY=wayland-0 .venv/bin/python server.py; echo "exit=$?"
```
Expected: stderr contains `Wayland` + `X11`, no traceback, `exit=2`. Normal start still works: `.venv/bin/python server.py &` then `curl -s localhost:8745/api/capabilities` shows `"backend": "linux"`, then stop it.

---

### Task 6: Absolute input via xdotool (mouse, keyboard, held tracking)

**Files:**
- Modify: `backends/linux.py` (replace mouse/keyboard stub bodies; add module-level helpers)
- Test: `tests/unit/test_linux_backend.py` (extend)

**Interfaces:**
- Consumes: Task 5's `require_x`, `backends.forbidden.assert_forbidden`.
- Produces (Tasks 7–10 rely on these):
  - `run_x11(cmd: list[str], timeout: float = 5.0) -> str` — generic runner; `cmd[0]` is the binary (`xdotool`/`wmctrl`/`xwininfo`/`sleep`); `subprocess.run(capture_output=True, text=True, timeout=timeout)`. `FileNotFoundError` → `ApiError("BACKEND_UNAVAILABLE", remediation="sudo apt install xdotool wmctrl")`; `subprocess.TimeoutExpired` → `ApiError("OPERATION_TIMEOUT", status=504)`; non-zero exit → `RuntimeError(stderr)`.
  - `type_segments(text: str) -> list[str]` — splits on `\n` (each segment typed separately with `Return` between; `\t` kept for `xdotool type`).
  - `KEYMAP: dict[str, str]` — API name → xdotool keysym: `enter→Return`, `esc/escape→Escape`, `space→space`, `tab→Tab`, `pageup→Page_Up`, `pagedown→Page_Down`, `backspace→BackSpace`, `delete→Delete`, `up/down/left/right→Up/Down/Left/Right`, `win→Super_L`, `home→Home`, `end→End`; `ctrl/alt/shift/super` and single chars pass through unchanged; `xdotool_key(name) -> str` applies it.
  - `mouse_move(x, y, duration=0.15)`, `mouse_click(x, y, button, clicks)`, `mouse_scroll(clicks, x, y)`, `mouse_drag(...)`, `mouse_move_relative(dx, dy)`, `mouse_down/up(button)`, `key_press/down/up(key)`, `key_hotkey(*keys)`, `type_text(text, interval)` as module functions + `LinuxBackend` bindings (signatures from `core/backends.py`)
  - Held tracking: `_held_keys: dict[str, str]` and `_held_buttons: dict[str, str]` (name → device source, default `"x11"`; Task 9 adds `"uinput"` tags), `threading.Lock`; `held_state()` returns `{"keys": sorted(_held_keys), "buttons": sorted(_held_buttons)}` (fresh dict); `release_all()` issues keyup/buttonup per item via its recorded source, clears the dicts, returns `{"ok": True, "released": [...]}` (item `"mouse:left"` for buttons, bare key name for keys; per-item failures appended as `"<name> (release failed: ...)"` — Windows parity). Never raises.

- [ ] **Step 1: Write failing tests** (extend `tests/unit/test_linux_backend.py`)
  - `test_keymap_enter_escape_arrows` — `xdotool_key("enter") == "Return"`, `xdotool_key("esc") == "Escape"`, `xdotool_key("up") == "Up"`, `xdotool_key("a") == "a"`, `xdotool_key("ctrl") == "ctrl"`.
  - `test_runner_raises_operation_timeout_on_hang` — `run_x11(["sleep", "10"], timeout=0.2)` → `ApiError` code `OPERATION_TIMEOUT`, status 504.
  - `test_runner_missing_binary` — `mock.patch.dict(os.environ, {"PATH": ""})` → `ApiError` `BACKEND_UNAVAILABLE`, remediation mentions `xdotool`.
  - `test_held_state_never_raises_without_xdotool` — PATH stripped, no display: `held_state()` still returns the exact dict; `release_all()` returns `{"ok": True, "released": []}` (Review Focus #1).
  - `test_type_text_splits_newlines` — `type_segments("a\nb") == ["a", "b"]` (Return goes between segments).
  - `@skipUnless(DISPLAY)` live group: `test_mouse_move_reaches_screen` (`mouse_move(100,100)` → `xdotool getmouselocation` reports x≈100), `test_type_text_into_xev` (start `xev`, focus it, `type_text("hi")`, assert KeyPress events h,i in output), `test_key_hotkey_blocked` (`key_hotkey("alt","f4")` raises `PermissionError`).

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** per Interfaces (stepped interpolation for `duration`: ≤20 steps of `mousemove_relative`; `type_text` one `xdotool type --delay <ms>` call per segment; scroll sign: `clicks>0 → button 4`, `<0 → button 5`; buttons 1/2/3).

- [ ] **Step 4: Run tests**
```bash
.venv/bin/python -m unittest tests.unit.test_linux_backend -v
```
Expected: all PASS (DISPLAY-gated tests run on this machine).

---

### Task 7: Window management

**Files:**
- Modify: `backends/linux.py` (replace window stub bodies)
- Test: `tests/unit/test_linux_backend.py` (extend)

**Interfaces:**
- Consumes: Task 5 (`require_x`), Task 6 (`run_x11`, `mouse_click` etc.).
- Produces:
  - `parse_wmctrl(line: str) -> dict` — pure; `wmctrl -lGpx` line → `{"hwnd": int(hex_id), "desktop": int, "rect": [x, y, x+w, y+h], "pid": int, "wm_class": str, "title": str}`
  - `parse_xwininfo_tree(text: str) -> list[dict]` — pure; child lines → `[{"hwnd": int, "class": str, "title": str}]`
  - `list_windows() -> list[dict]` — spec §4 fields exactly; `process` from `/proc/<pid>/comm` or `"?"`; empty titles filtered; `focused` = `hwnd == xdotool getactivewindow`
  - `get_focused_window() -> dict | None`, `focus_window(hwnd)` (verify active else `RuntimeError`), `close_window(hwnd, expect_title, expect_process)` (Windows-parity refusal dicts, then `wmctrl -ic`, 1s poll → `{"ok","closed","title","note"}`), `maximize_window`, `set_topmost`, `kill_process(pid)` (`{"ok","output"}`), `process_name(pid)` (`/proc/<pid>/comm` or `None`), `probe_input_mode(hwnd)` — never raises: `"invalid"` when `display_server() != "x11"` or hwnd unknown, else `"focused"` (pins the Task 5 fail-safe test), `list_children`, `pick_input_child`, `client_to_screen(hwnd, x, y) -> (sx, sy)` (`ValueError` if gone), and focus-first `window_type_text/key/hotkey/click/scroll/drag` returning the Windows dict shapes (`{"ok","chars"}`, `{"ok","vk"}` with vk = X keysym int, `{"ok","x","y","button","clicks"}`, `{"ok","clicks"}`, `{"ok","from","to"}`). `window_key`/`window_hotkey` call `assert_forbidden` first → `PermissionError` (server 403).

- [ ] **Step 1: Write failing tests**
  - Fixture tests (CI-safe): `test_parse_wmctrl_line` with a real captured sample line → exact dict incl. `rect` as `[l,t,r,b]`; `test_parse_wmctrl_skips_malformed` (garbage → `None`/skip); `test_parse_xwininfo_tree_children` with sample tree text → children list with class+title.
  - `test_close_window_refuses_title_mismatch` (no display needed if refusal happens before X call — structure the method that way): `close_window(999, expect_title="Nope")` → `{"ok": False, ...}` not an exception.
  - `@skipUnless(DISPLAY)`: `test_list_windows_contains_known_window`, `test_focus_window_makes_it_active` (open two `xmessage`/`xterm`-style windows, focus second, `xdotool getactivewindow` equals it — Review Focus #2), `test_focus_unknown_hwnd_raises_runtime`, `test_client_to_screen_matches_geometry`.

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** per Interfaces (validation before any X call in `close_window`/`kill_process`; `kill_process` refuses `pid <= 1` and `pid == os.getpid()`).

- [ ] **Step 4: Run tests** → PASS.

---

### Task 8: Capture + imageops wiring

**Files:**
- Modify: `backends/linux.py` (replace capture stub bodies)
- Test: `tests/unit/test_linux_backend.py` (extend)

**Interfaces:**
- Consumes: Task 3 (`imageops.encode_jpeg/scale_image/frame_diff`), Task 6 (`run_x11`).
- Produces: `screen_size(monitor=1) -> (w, h)`, `list_monitors() -> [{"id","name","left","top","width","height","is_primary"}]` (mss semantics, 1-based), `screenshot(monitor=1, region=None) -> Image RGB`, `screenshot_jpeg(...) -> bytes` (via `imageops.encode_jpeg`), `screenshot_scaled(...) -> Image` (via `imageops.scale_image`), `frame_diff(prev, cur) -> dict` (delegates), `capture_window(hwnd, client_only=False) -> Image` (ImageMagick `import -window <id> png:-` when `shutil.which("import")`, else mss region from window geometry; failure → `RuntimeError` so the server returns 409).

- [ ] **Step 1: Write failing tests**
  - `@skipUnless(DISPLAY)`: `test_screen_size_matches_xrandr` (compare with `xdotool getdisplaygeometry`), `test_screenshot_region_size` (`screenshot(region=(0,0,50,40))` → (50,40)), `test_screenshot_jpeg_magic_bytes` (`b"\xff\xd8"` prefix), `test_screenshot_scaled_half`, `test_list_monitors_shape` (first entry `id==1`, `is_primary is True`), `test_frame_diff_between_two_captures` (returns the dict shape), `test_capture_window_of_active_window` (captured image size > 10x10).

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** per Interfaces.

- [ ] **Step 4: Run tests** → PASS.

---

### Task 9: Game mode (uinput) + key routing

**Files:**
- Modify: `backends/linux.py` (replace `game_*` stubs; route `key_down/up/press` when active)
- Test: `tests/unit/test_linux_backend.py` (extend)

**Interfaces:**
- Consumes: Task 4 (`UInputDevice`, `key_to_code`, `button_to_code`, `uinput_permission_error`), Task 6 (held sets + xdotool path).
- Produces:
  - `game_start(sensitivity=12) -> {"ok": True, "center": [cx, cy], "sensitivity": int, "note": "cursor locked to center; use /api/game/move for camera look; X11 cannot clip the cursor — the game must capture the pointer"}` — first `release_all()`, then open `UInputDevice` (failure → `uinput_permission_error("game_start")`)
  - `game_move(dx, dy, sensitivity=12) -> {"ok": True}` — `device.move_rel(dx*sensitivity, dy*sensitivity)`
  - `game_stop() -> {"ok": True, "released": [...]}` — release everything held on both sources, close device, clear flag
  - `game_active() -> bool` — internal flag only
  - Routing: while active, `key_down/up/press` and `mouse_down/up` use the uinput device; each held item records its source (`"x11"` or `"uinput"`) so `release_all` releases on the right source.

- [ ] **Step 1: Write failing tests**
  - `test_release_all_releases_both_sources` (Review Focus #4): with the device source-tagging logic, hold key `"w"` tagged `"x11"` and `"a"` tagged `"uinput"` (inject via the internal hold helper with a mocked sender), `release_all()` releases both, both held dicts end up empty, `released` has 2 entries.
  - `test_game_move_applies_sensitivity`: mocked device records `move_rel(dx*s, dy*s)` for `(game_move(3, -2, sensitivity=10))` → `(30, -20)`.
  - `test_game_start_releases_first`: mocked `release_all` called before device open.
  - `test_game_active_is_plain_flag`: after construction with no display, `game_active() is False`; never raises.
  - `@skipUnless(DISPLAY and os.access("/dev/uinput", os.R_OK|os.W_OK))`: `test_game_mode_uinput_events_reach_x11` — start `xev`, focus it, `game_start()`, `game_move(5, 0, 1)` → motion event delta ≥ 1 observed in `xev` output; `key_down("w")` → KeyPress for w; `game_stop()` → `game_active() is False`.

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement** per Interfaces.

- [ ] **Step 4: Run tests** → PASS.

---

### Task 10: Live acceptance suite (`test-linux.py`) — the 7 required items

**Files:**
- Create: `test-linux.py`

**Interfaces:**
- Consumes: running server (`.venv/bin/python server.py`), `.token`, endpoints from `AGENT_GUIDE.md`; pattern copied from `test-security.py` (`urllib`, `check(name, cond, detail)`).
- Produces: one PASS/FAIL/SKIP line per acceptance item + exit code 0 only if no FAIL.

- [ ] **Step 1: Write `test-linux.py`** with these checks (each mirrors an ADIM 5 item):
  1. `mouse move+click` — `POST /api/mouse {"action":"move","x":200,"y":300}` → `xdotool getmouselocation` within ±3 px; then `{"action":"click","x":200,"y":300}` on a spawned `xev` window (click first brings focus — use `expect_hwnd`-free focused path) → xev log contains `buttonpress`.
  2. `keyboard type + hotkey` — focus a `xev` window, `POST /api/key {"action":"type","text":"Merhaba 123"}` → xev shows those KeyPresses; `{"action":"hotkey","keys":["ctrl","a"]}` (to a text target) or xev shows ctrl+a sequence.
  3. `screenshot` — `GET /api/screenshot` → bytes start with JPEG magic, size > 10 KB; save to `/tmp/sc-linux-test.jpg`.
  4. `ocr` — render known text (spawn `xmessage "SCREEN-CONTROL-OCR-OK"`), `POST /api/ocr {"region": <message rect>}` → response text contains `SCREEN-CONTROL-OCR-OK`.
  5. `windows list + focus` — `GET /api/windows` returns ≥1 entry with all 6 fields; spawn two windows, `POST /api/window {"action":"focus","hwnd":A}` → `GET /api/window`/`get_focused_window` reports A; plus **5b** (Review Focus #3): `POST /api/window/post {"action":"click","hwnd":A,"x":50,"y":60}` → `getmouselocation` ≈ client origin + (50,60).
  6. `game mode` — `POST /api/game {"action":"start"}` → center/sensitivity keys present; `{"action":"move","dx":40,"dy":-10}` observed as motion on an `xev` target; `key` `down` w → KeyPress, hold 1.5 s, `release_all` → no stuck key; `{"action":"stop"}` → `game_active` false. (No native Linux game installed — mechanics verified on `xev`; reported as such.)
  7. `CS2 offline input` — printed as `SKIP: CS2 not installed on Linux (Windows Steam library only)`.

- [ ] **Step 2: Start the server and run the suite**
```bash
.venv/bin/python server.py > /tmp/sc-server.log 2>&1 & sleep 3
.venv/bin/python test-linux.py
```
Expected: items 1–6 PASS, item 7 SKIP, exit code 0. Fix backend issues until true; record any item that cannot pass as FAIL in the report (do not weaken the check).

- [ ] **Step 3: Stop the server**
```bash
pkill -f "\.venv/bin/python server.py" || true
```

---

### Task 11: Docs, ROADMAP, final regression

**Files:**
- Modify: `README.md` (new "Linux (X11)" section), `ROADMAP.md` (Phase 5 checkboxes)

**Interfaces:**
- Consumes: everything above (documented behavior must match the code).
- Produces: user-facing setup + limitations documentation.

- [ ] **Step 1: README Linux section** — requirements (Ubuntu/Mint-family, X11 session check `echo $XDG_SESSION_TYPE`, apt list), one-command setup (`./install-linux.sh`), uinput permissions (both remediations from spec §5.4 + how to verify `[ -w /dev/uinput ]`), how to run (`./start-server.sh`), platform support matrix row updated from "fail-closed stub" to supported-with-limits, and Known limitations: X11-only (Wayland → clear 501), background input = focus-stealing, no cursor clip in game mode, `virtual_desktops` False (`/api/desktops` 503), fractional scaling untested, `capture_window` falls back to region capture when occluded.

- [ ] **Step 2: ROADMAP Phase 5** — tick the checkboxes actually delivered (screen capture, absolute mouse, keyboard, window listing, active window, focus, X11 diagnostics, Ubuntu smoke tests, limitations note); leave "Test GNOME/KDE/XFCE" unticked.

- [ ] **Step 3: Final regression (evidence before claiming done)**
```bash
.venv/bin/python -m unittest discover -s tests/unit -t . -v 2>&1 | tail -6
git diff --ignore-cr-at-eol --stat
git status --short
```
Expected: 0 failures / 0 errors (baseline 25 pass now higher, 4 win32 skips); diff shows only the files in spec §6; no `windows.py` change beyond the guard.

- [ ] **Step 4: Report to the user (Turkish)** — yapılanlar, ADIM 5 test sonuçları (7 madde: geçen/kalan/atlanan), çalışmayanlar, bilinen sınırlar, sudo ile yapılanlar.
