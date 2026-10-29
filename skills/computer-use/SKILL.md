---
name: computer-use
description: Gives an agent live eyes and hands on this computer's desktop — screenshot, screen OCR, mouse, keyboard, window management and app launching — through the local screen-control server (REST or MCP) on Windows and Linux X11. Use when the user asks to see or read the screen, click or type into a GUI application, open an app, or automate anything visible on the desktop.
---

# Computer Use (screen-control)

## Overview

`screen-control` is a local server that turns HTTP (or MCP) calls into safe
desktop actions. Its core loop is **perceive → act → verify**: read the
screen, perform ONE action, read again — never assume an action landed.

## Setup (once per machine)

1. Install: `git clone https://github.com/Xeakaes/computer-use-for-all-agents`
   then `pip install -r requirements.txt` (Linux also has `./install-linux.sh`).
2. Start the server: `./start-server.sh` (Linux) / `start-server.bat`
   (Windows) / `python server.py` → `http://127.0.0.1:8745`.
3. Token: read `.token` next to `server.py`. It is **regenerated on every
   server start** — never cache it across restarts. Send it as the
   `X-Auth-Token` header on every call. POST bodies need
   `Content-Type: application/json` (otherwise 415).
4. Route yourself first: `GET /api/capabilities` → `backend` is
   `windows`, `linux`, or `macos`. Follow platform notes below.

The MCP transport (`mcp_server.py`, 16 tools) does **not** start the REST
server — start `server.py` first; MCP reads `.token` automatically.

## The loop

```bash
TOKEN=$(cat /path/to/screen-control/.token)
H="X-Auth-Token: $TOKEN"

# 1. PERCEIVE — fast frame for visuals, OCR for text
curl -H "$H" "http://127.0.0.1:8745/api/vision/frame?scale=0.5" -o /tmp/f.jpg

# 2. ACT — one action, guarded by the target window
curl -X POST http://127.0.0.1:8745/api/key -H "$H" \
  -H "Content-Type: application/json" \
  -d '{"action":"type","text":"hello","expect_hwnd":12345}'

# 3. VERIFY — read again; on 409 re-focus the window and retry once
curl -H "$H" -o /tmp/check.jpg http://127.0.0.1:8745/api/screenshot
```

Delete screenshots/frames after review (`rm /tmp/f.jpg`) — privacy policy
of this tool.

## Quick reference

| I need to… | Call |
|---|---|
| See the screen | `GET /api/screenshot` (jpeg) or `GET /api/vision/frame?scale=0.5` (~40 ms) |
| Read screen text | `POST /api/ocr` body `{"region":[x,y,w,h]}` optional |
| Move / click | `POST /api/mouse` `{"action":"click","x":N,"y":N}` |
| Scroll | `POST /api/mouse` `{"action":"scroll","clicks":-3}` |
| Type text | `POST /api/key` `{"action":"type","text":"...","expect_hwnd":N}` |
| Press / combo | `POST /api/key` `{"action":"press","key":"enter"}` / `{"action":"hotkey","keys":["ctrl","s"]}` |
| List windows | `GET /api/windows` → `hwnd`, `title`, `pid`, `focused`, `rect` |
| Focus a window | `POST /api/window` `{"action":"focus","hwnd":N}` |
| Safe close | `POST /api/window` `{"action":"close","hwnd":N,"expect_title":"..."}` |
| Open an app / URL / file | `POST /api/launch` `{"app":"firefox"}` (exactly one of `app`/`url`/`file`) |
| Wait for a window | `GET /api/window/wait?title=Notepad&timeout=10` → 408 on timeout |
| Emergency unstuck | `POST /api/release_all` |

Full endpoint catalogue: `references/api.md` (read it when the quick
reference is not enough).

## Safety rules

- **Always pass `expect_hwnd`** on typing/clicking into a window: the
  server verifies the foreground window and refuses with **409
  FOCUS_MISMATCH** otherwise. This is what keeps keystrokes out of the
  wrong window.
- Blocked everywhere (**403 INPUT_BLOCKED**): `alt+f4`, `win`/`super`,
  `ctrl+alt+del` and friends. Close windows via
  `POST /api/window {"action":"close"}` with `expect_title`.
- `POST /api/launch` never runs a shell: `app` is a plain `PATH` command
  (interpreter/executor names like `python`/`sh`/`cmd`/`env` are refused 400)
  name (resolved with PATHEXT on Windows — `"notepad"` works; on Linux
  try `firefox`, `gedit`, …), `url`/`file` go through `xdg-open`/`open`.
  `expect_title` matches the window title with the **same case-insensitive
  substring rule** as `/api/window/wait`, and the response may contain
  `"window": null` on HTTP 200 — the app spawned, the window just has not
  appeared yet; poll `GET /api/window/wait` or re-list. `wait` returns the
  first match; to target a specific window, list via `GET /api/windows`
  and act on its `hwnd`.
- A 30 s watchdog releases stuck input; `POST /api/release_all` is the
  manual stop.

## Platform notes

**Linux (X11)**
- OCR is slow (~5 s full screen, ~1.3 s region): prefer
  `/api/vision/frame` for visual checks, OCR only for text.
- "Background" input (`POST /api/window/post`) is focus-first — it steals
  focus; Windows-style background typing does not exist here. Flow:
  focus → guarded type.
- `hwnd` is an X11 window id; window entries have no `desktop` field and
  `GET /api/desktops` returns 503.
- **Wayland fails fast**: the server refuses to start
  (`UNSUPPORTED_DISPLAY_SERVER`, exit code 2 / HTTP 501). Do not retry —
  the fix is an X11 session (`XDG_SESSION_TYPE=x11`).

**Windows**
- Reference backend: background typing works, virtual desktops
  (`/api/desktops`, `/api/desktop`) supported, focus guard identical.
- To type into a window **without** stealing focus (e.g. a chat app in
  the background), use `POST /api/window/post` with
  `"mode":"background"` (check `GET /api/window/input-mode` first);
  for everything else the focus + `expect_hwnd` loop above is the
  default.

**macOS**
- Fail-closed stub: every action returns `BACKEND_UNAVAILABLE` (501).

## Common mistakes

| Symptom | Cause / fix |
|---|---|
| 401 on every call | `.token` was regenerated by a server restart — re-read it |
| 415 | Missing `Content-Type: application/json` on a POST |
| 409 FOCUS_MISMATCH | Focus another window first, or the target hwnd is stale — re-list windows |
| `window: null` with 200 from launch | App spawned, window not ready — wait with `/api/window/wait` |
| Typing landed in the wrong window | You skipped `expect_hwnd` — always pass it |
| OCR takes seconds | Full-screen OCR is slow — use `/api/vision/frame` |
| `region` rejected | Both `"x,y,w,h"` string and `[x,y,w,h]` array are accepted by OCR; screenshot GETs use `?region=x,y,w,h` |
| 501 / server exits on start | Wayland session — switch to X11, do not retry |
