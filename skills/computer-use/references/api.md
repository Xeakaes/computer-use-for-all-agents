# screen-control API reference

Base URL: `http://127.0.0.1:8745` — every `/api/*` call needs the
`X-Auth-Token: <token>` header (token = `.token` file, regenerated on each
server start; `GET /token` also returns it). POST bodies are JSON and need
`Content-Type: application/json`.

Errors: `{ "ok": false, "error": "..." }` with a machine-readable code in
`error.code` (JSON envelope) or a plain string (validation). Status codes:
400 validation · 401 auth · 403 blocked input · 404 window/process not
found · 409 focus mismatch · 408 wait timeout · 415 missing content type ·
500 unexpected · 501 unsupported platform/backend · 503 capability
unavailable (OCR engine, virtual desktops).

Error codes: `PERMISSION_REQUIRED`, `UNSUPPORTED_PLATFORM`,
`UNSUPPORTED_DISPLAY_SERVER`, `BACKEND_UNAVAILABLE`, `WINDOW_NOT_FOUND`,
`FOCUS_MISMATCH`, `CAPTURE_FAILED`, `INPUT_BLOCKED`, `INVALID_TARGET`,
`RESOURCE_LIMIT`, `OPERATION_TIMEOUT`.

## Discovery

| Endpoint | Purpose |
|---|---|
| `GET /api/capabilities` | Backend name + capability matrix. **Call this first.** |
| `GET /api/info` | Screen size, monitors, OCR availability, watchdog state |
| `GET /api/monitors` | Monitor list with rects |
| `GET /api/held` | Currently held keys/buttons, game mode, watchdog idle time |
| `GET /api/session` | Recorded API calls (debug/replay), `?since=<float>` |
| `POST /api/session/clear` | Clear the session log |

## Perception

| Endpoint | Parameters | Notes |
|---|---|---|
| `GET /api/screenshot` | `?monitor=1`, `?region=x,y,w,h`, `?scale=`, `?quality=` | Full JPEG |
| `GET /api/vision/frame` | `?scale=`, `?gray=0|1`, `?quality=`, `?region=x,y,w,h` | Fast frame (~40 ms) — prefer for loops |
| `GET /api/stream` | `?fps=`, `?quality=` | MJPEG live stream (`<img src>` compatible) |
| `POST /api/vision/diff` | `{"region":[x,y,w,h]}` | Inter-frame motion summary (text, no vision model) |
| `POST /api/ocr` | `{"region":[x,y,w,h]}` or `{"region":"x,y,w,h"}`, `{"scan_focus":bool}` | Returns `text`, `lines`, `items[{text,x,y}]`. Slow on Linux (~5 s full screen) |

## Input

| Endpoint | Body | Notes |
|---|---|---|
| `POST /api/mouse` | `action`: `move` / `click` (`button`, `clicks`) / `scroll` (`clicks`) / `drag` (`x2`,`y2`) / `down` / `up` + `x`,`y` | Coordinates are screen pixels |
| `POST /api/key` | `action`: `type` (`text`) / `press` (`key`) / `hotkey` (`keys`) / `down` / `up`; optional `expect_hwnd` | `expect_hwnd` = focus guard; mismatch → 409. **Always pass it.** |
| `POST /api/release_all` | `{}` | Emergency: release every held key/button |
| `POST /api/keys` | `{"action":"create","name":"k","expires_in_hours":24}` | Scoped API keys for headerless clients |

## Windows

| Endpoint | Body / params | Notes |
|---|---|---|
| `GET /api/windows` | — | `hwnd`, `title`, `process`, `pid`, `focused`, `rect` (+`desktop` on Windows) |
| `POST /api/window` | `action`: `focus` / `close` (`expect_title`) / `kill` (`pid`) / `topmost` / `untopmost` / `maximize` + `hwnd` | Safe close = WM_CLOSE + verify |
| `GET /api/window/wait` | `?title=`, `?timeout=10` (max 30) | Blocks until title substring appears; 408 `OPERATION_TIMEOUT` |
| `GET /api/window/capture` | `?hwnd=`, `?client=1` | PrintWindow capture without focus stealing |
| `GET /api/window/children` | `?hwnd=` | Child controls (class, title, hwnd) |
| `GET /api/window/input-mode` | `?hwnd=` | `focused` / `postmessage` / `uia` / `invalid` — check before `/api/window/post` |
| `POST /api/window/post` | `action`: `type` / `key` / `hotkey` / `click` / `scroll` / `drag`, `mode`: `auto` / `background` / `focused` + `hwnd` | Auto path selection; on Linux X11 it is focus-first (steals focus) |

## Apps & desktops

| Endpoint | Body | Notes |
|---|---|---|
| `POST /api/launch` | exactly one of `app` / `url` / `file`; `args` (app only); `expect_title`; `timeout` (≤30) | Never a shell. Interpreter/executor app names (`python*`, `sh`, `cmd`, `env`, …) are refused 400. Returns `pid` (+ `window` if `expect_title`; may be `null` on 200) |
| `GET /api/desktops` | — | Virtual desktops (Windows; 503 elsewhere) |
| `POST /api/desktop` | `action`: `switch` (`n`) / `create`; `hwnd` to move | Never switches silently |

## Game mode

| Endpoint | Body | Notes |
|---|---|---|
| `POST /api/game` | `action`: `start` (`sensitivity`) / `move` (`dx`,`dy`) / `stop` / `heartbeat` | Cursor physically locked to screen center while active; 30 s watchdog; heartbeat keeps long holds alive |

## Security model (what the server enforces for you)

- Per-start session token; optional scoped keys with expiry.
- Deadly shortcut combos (`alt+f4`, `win`, `ctrl+alt+del`, …) → 403.
- Focus guard: keystrokes with `expect_hwnd` against a different
  foreground window → 409 `FOCUS_MISMATCH`.
- Dangerous process kills deny-listed (session/system processes).
- Input watchdog auto-releases stuck keys after 30 s.
- Screenshot cleanup policy: delete captures after review.
