# 🤖 Screen Control — AI Agent Integration Guide

> This guide is written for AI agents (LLMs, vision models, automation
> frameworks) that will control a machine through the Screen Control API.
> For general usage, see [README.md](README.md).

---

## Table of Contents

- [Quick Start for Agents](#quick-start-for-agents)
- [Performance Note](#performance-note)
- [MCP Mode (Native Tool Calling)](#mcp-mode-native-tool-calling)
- [Perceive-Act Loop](#perceive-act-loop)
- [Choosing a Perception Mode](#choosing-a-perception-mode)
- [Authentication](#authentication)
- [Core Workflow: App Automation](#core-workflow-app-automation)
- [Core Workflow: Game Control](#core-workflow-game-control)
- [Focus Guard (expect_hwnd)](#focus-guard-expect_hwnd)
- [Reading the Screen](#reading-the-screen)
- [Sending Input](#sending-input)
- [Window Management](#window-management)
- [Focus-Free (Background) Control](#focus-free-background-control)
- [Virtual Desktops](#virtual-desktops)
- [Game Mode](#game-mode)
- [Vision Access for Image Models](#vision-access-for-image-models)
- [Text-Based Motion Detection](#text-based-motion-detection)
- [Bandwidth Optimization](#bandwidth-optimization)
- [Error Handling](#error-handling)
- [Safety Guarantees](#safety-guarantees)
- [Complete curl Examples](#complete-curl-examples)

---

## Quick Start for Agents

```bash
# 1. Read the token
TOKEN=$(cat .token)

# 2. Check system state
curl -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8745/api/info

# 3. List windows
curl -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8745/api/windows

# 4. Take a screenshot
curl -H "X-Auth-Token: $TOKEN" -o screen.jpg http://127.0.0.1:8745/api/screenshot

# 5. Read on-screen text
curl -X POST http://127.0.0.1:8745/api/ocr -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{}'

# 6. Click somewhere
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"click","x":500,"y":300}'
```

---

## Performance Note

The API adds only milliseconds per call — **you** are the bottleneck. Your
effective speed and capability are bounded by your own inference latency,
reasoning depth, context window and runtime environment (network round-trips,
tool-call limits). Choose tasks accordingly:

- Fast inference + tight tool loop → real-time and action-heavy tasks (game
  mode, live UI navigation) are feasible.
- Slower loop → prefer deliberate, verification-heavy tasks and lean payloads
  (see [Bandwidth Optimization](#bandwidth-optimization)); every screen read
  you make costs context, so read regions, not whole screens.

The perceive-act loop below runs at *your* pace — the server never rate-limits
you, and never thinks for you.

---

## MCP Mode (Native Tool Calling)

If you (the agent) connect through **MCP** instead of raw HTTP, the same
capabilities are exposed as native tools by `mcp_server.py` — no curl, no
token handling, no manual JSON. All safety rules still apply (they live in
`server.py`).

### Connect

- **Desktop clients** (Claude Desktop, Cursor, VS Code): run `mcp_server.py`
  as an stdio MCP server (config in README → *MCP Support*).
- **Cloud clients**: the operator runs `start-server.bat` — it starts the
  REST server, the MCP HTTP server, and a cloudflared tunnel, then prints the
  public MCP URL and the token in the terminal. Manual equivalent:
  `python mcp_server.py --http --port 8751` +
  `cloudflared tunnel --url http://127.0.0.1:8751`. The HTTP endpoint requires
  the `X-Auth-Token` header on every request — query-string tokens
  (`?token=...`) are rejected by design. The operator's token is in `.token`.

### Tool mapping (HTTP → MCP)

| You want to... | MCP tool | Notes |
|---|---|---|
| Check system state | `get_info` | replaces `GET /api/info` |
| Read text on screen | `ocr_screen` | replaces `POST /api/ocr` |
| **See** the screen (vision models) | `screenshot` | returns a real image block; `scale`/`quality` to save context |
| Detect what changed | `motion_diff` | replaces `POST /api/vision/diff` |
| Move / click / drag | `mouse` | one tool, `action` parameter |
| Type / press / hotkey | `keyboard` | `expect_hwnd` supported — use it |
| List / focus / close windows | `list_windows`, `focus_window`, `close_window` | close is verified, never blind Alt+F4 |
| Inspect child controls of a classic app | `window_children` | useful for targeting an Edit box directly |
| Background input into an app | `window_post` | auto-routes WinUI/UWP apps |
| Read a background app | `window_capture_ocr` | no focus steal |
| Play a game | `game` | start / move / stop / heartbeat |

The perceive-act loop, perception-mode selection and all workflow guidance
in this guide apply identically in MCP mode — only the transport changes.

---

## Perceive-Act Loop

Every interaction **must** follow this pattern.  Pre-written command chains
are dangerous — the screen state can change at any time.

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  READ    │────▶│  PLAN    │────▶│   ACT    │────▶│  VERIFY  │
│          │     │          │     │          │     │          │
│ OCR      │     │ Decide   │     │ One      │     │ Re-read  │
│ Vision   │     │ next     │     │ command  │     │ screen   │
│ Windows  │     │ action   │     │ only     │     │ to check │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
      ▲                                                  │
      └──────────────────────────────────────────────────┘
```

1. **READ** — OCR or vision reads the screen; window list shows what's open
2. **PLAN** — decide the next action based on what was actually seen
3. **ACT** — send a **single** command
4. **VERIFY** — re-read the screen to confirm the action had the intended effect

**Why single commands?** Because:
- Windows can steal focus at any time
- Dialogs can appear unexpectedly
- The screen state changes continuously
- Error recovery requires fresh perception

---

## Choosing a Perception Mode

The server exposes four perception endpoints.  **You choose which one to use
based on two factors:** (1) the nature of the task, and (2) whether you can
consume images directly.

| Scenario | Endpoint | Why |
|---|---|---|
| Static, text-heavy UI (forms, menus, settings) | `POST /api/ocr` | Extracts text + clickable coordinates; cheap, no vision needed |
| One-shot visual check (icon present? colour correct? layout OK?) | `GET /api/vision/frame` | Single image frame; use `?scale=0.5&gray=1` to save tokens |
| Fast-changing scene (game, real-time tracking) | `GET /api/stream` | Continuous MJPEG feed; pair with `?fps=5&scale=0.5` for efficiency |
| Visual model unavailable, but "what changed?" is needed | `POST /api/vision/diff` | Returns a text-based tile map of changed regions + coordinates |

**Decision rule:**

1. **Can you see images?** → prefer `vision/frame` or `stream`; fall back to
   `ocr` for text and `diff` for motion.
2. **Text-only agent?** → use `ocr` for reading and `diff` for detecting
   changes.  You can still verify actions by checking OCR output before/after.

Mix freely — there is no single "correct" mode.  For example, in a game you
might use `stream` for continuous monitoring but switch to `ocr` to read a
dialogue box, then back to `diff` to confirm an animation finished.

For payload sizes and tuning, see [Bandwidth Optimization](#bandwidth-optimization).

---

## Authentication

Every request requires the `X-Auth-Token` header.

```bash
TOKEN=$(cat .token)
```

```bash
# All requests must include this header
-H "X-Auth-Token: $TOKEN"
```

| Scenario | Response |
|---|---|
| Missing token | `401 Unauthorized` |
| Wrong token | `401 Unauthorized` |
| POST without `Content-Type: application/json` | `415 Unsupported Media Type` |

---

## Core Workflow: App Automation

### Step-by-step

```bash
# 1. Find the target window
WINDOWS=$(curl -s -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8745/api/windows)
# Parse JSON to find the hwnd of your target app

# 2. Focus it
curl -X POST http://127.0.0.1:8745/api/window -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hwnd":TARGET_HWND,"action":"focus"}'

# 3. Read current state
curl -X POST http://127.0.0.1:8745/api/ocr -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{}'

# 4. Perform ONE action (e.g., type text)
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"type","text":"Hello World","expect_hwnd":TARGET_HWND}'

# 5. Verify the result
curl -X POST http://127.0.0.1:8745/api/ocr -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{}'

# 6. Repeat from step 4
```

### Key commands for app automation

| Task | API Call |
|---|---|
| Type text | `POST /api/key {"action":"type","text":"..."}` |
| Press Enter | `POST /api/key {"action":"press","key":"enter"}` |
| Ctrl+S (save) | `POST /api/key {"action":"hotkey","keys":["ctrl","s"]}` |
| Click a button | `POST /api/mouse {"action":"click","x":N,"y":N}` |
| Scroll down | `POST /api/mouse {"action":"scroll","clicks":-3}` |
| Read the screen | `POST /api/ocr {}` |
| Find a window | `GET /api/windows` |
| Close a window | `POST /api/window {"action":"close","hwnd":N}` |
| Kill a process | `POST /api/window {"action":"kill","hwnd":N,"pid":N}` |

---

## Core Workflow: Game Control

### Step-by-step

```bash
# 1. Focus the game window
curl -X POST http://127.0.0.1:8745/api/window -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hwnd":GAME_HWND,"action":"focus"}'

# 2. Enter game mode (locks cursor to center)
curl -X POST http://127.0.0.1:8745/api/game -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"start","sensitivity":12}'

# 3. Look around (relative mouse = camera rotation)
curl -X POST http://127.0.0.1:8745/api/game -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"move","dx":50,"dy":0}'

# 4. Walk forward (hold W)
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"down","key":"w"}'

# 5. ... walk for a while ...

# 6. Release W
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"up","key":"w"}'

# 7. Place a block (right-click)
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"click","button":"right","x":960,"y":540}'

# 8. Exit game mode when done
curl -X POST http://127.0.0.1:8745/api/game -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"stop"}'
```

### Game mode key commands

| Task | API Call |
|---|---|
| Start game mode | `POST /api/game {"action":"start","sensitivity":12}` |
| Look around | `POST /api/game {"action":"move","dx":N,"dy":N}` |
| Stop game mode | `POST /api/game {"action":"stop"}` |
| Hold W (walk) | `POST /api/key {"action":"down","key":"w"}` |
| Release W | `POST /api/key {"action":"up","key":"w"}` |
| Left click (mine) | `POST /api/mouse {"action":"down","button":"left"}` |
| Right click (place) | `POST /api/mouse {"action":"click","button":"right","x":N,"y":N}` |
| Select hotbar slot | `POST /api/key {"action":"press","key":"1"}` |
| Open inventory | `POST /api/key {"action":"press","key":"e"}` |
| Keep alive (long hold) | `POST /api/game {"action":"heartbeat"}` |

### Suitability matrix

| Game Type | Suitable? | Notes |
|---|---|---|
| Minecraft (building) | ✅ Yes | Place blocks, walk, mine |
| Minecraft (PvP) | ❌ No | Too slow for fast combat |
| Turn-based games | ✅ Yes | Ample time for read→act→verify |
| RPG / adventure | ✅ Yes | Inventory, dialogue, exploration |
| Fast FPS | ❌ No | Reaction time insufficient |
| Puzzle games | ✅ Yes | Click-based, read-heavy |

---

## Focus Guard (expect_hwnd)

The `expect_hwnd` parameter is **critical** for preventing "wrong window"
accidents.  When you pass it to `/api/key`, the server checks that the
foreground window matches before sending any input.

```bash
# SAFE: typing only goes to the correct window
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"type","text":"Hello","expect_hwnd":12345}'
```

If the foreground window is different, the server returns **409** and the
typing is refused:

```json
{
  "ok": false,
  "error": "Focus mismatch: foreground is chrome.exe — typing to the wrong window has been blocked"
}
```

**Always use `expect_hwnd`** when typing into a specific application.

---

## Reading the Screen

### OCR (text extraction)

```bash
# Full screen
curl -X POST http://127.0.0.1:8745/api/ocr -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{}'

# Specific region (much faster — ~10x for small areas)
curl -X POST http://127.0.0.1:8745/api/ocr -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"region":[0,0,800,100]}'
```

Response format:

```json
{
  "ok": true,
  "text": "File Edit View Help\nHello World",
  "lines": ["File Edit View Help", "Hello World"],
  "items": [
    {"text": "File Edit View Help", "x": 200, "y": 12},
    {"text": "Hello World", "x": 960, "y": 300}
  ]
}
```

Each `item` includes the **center coordinates** (x, y) of the text — you can
use these to click on specific buttons, menu items, or text fields.

### Vision (image frames)

For models that can process images directly:

```bash
# Single frame as base64 (for text-only API transports)
curl "http://127.0.0.1:8745/api/vision/frame?scale=0.5&gray=1&format=base64" \
  -H "X-Auth-Token: $TOKEN"

# MJPEG live stream (for continuous monitoring)
curl "http://127.0.0.1:8745/api/stream?fps=5&scale=0.5" \
  -H "X-Auth-Token: $TOKEN" -o stream.mjpg
```

---

## Sending Input

### Mouse

```bash
# Move cursor
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"move","x":500,"y":300}'

# Left click
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"click","x":500,"y":300,"button":"left"}'

# Right click
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"click","button":"right","x":960,"y":540}'

# Double-click
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"click","clicks":2,"x":500,"y":300}'

# Scroll
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"scroll","clicks":-3}'

# Drag
curl -X POST http://127.0.0.1:8745/api/mouse -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"drag","x1":100,"y1":100,"x":300,"y":300}'
```

### Keyboard

```bash
# Single key press
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"press","key":"enter"}'

# Key combination
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"hotkey","keys":["ctrl","c"]}'

# Type text (Unicode, layout-independent)
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"type","text":"Merhaba dünya 🌍"}'

# Hold key down (for games)
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"down","key":"w"}'

# Release held key
curl -X POST http://127.0.0.1:8745/api/key -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"up","key":"w"}'
```

---

## Window Management

### Find windows

```bash
curl -s -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8745/api/windows | python -c "
import sys, json
for w in json.load(sys.stdin)['windows']:
    mark = '*' if w['focused'] else ' '
    print(f\"{mark} hwnd={w['hwnd']} pid={w['pid']} {w['title'][:60]}\")"
```

### Focus a window

```bash
curl -X POST http://127.0.0.1:8745/api/window -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hwnd":12345,"action":"focus"}'
```

### Safe close (with verification)

```bash
curl -X POST http://127.0.0.1:8745/api/window -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"close","hwnd":12345,"expect_title":"Notepad"}'
```

### Force kill (task-manager style)

```bash
curl -X POST http://127.0.0.1:8745/api/window -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"kill","hwnd":12345,"pid":7890}'
```

---

## Focus-Free (Background) Control

Read and control windows **without stealing focus** — the user keeps working
on their main desktop.

### Capture a background window

```bash
# As PNG image
curl "http://127.0.0.1:8745/api/window/capture?hwnd=12345" \
  -H "X-Auth-Token: $TOKEN" -o window.png

# As OCR text (one step)
curl "http://127.0.0.1:8745/api/window/capture?hwnd=12345&ocr=1" \
  -H "X-Auth-Token: $TOKEN"
```

### Type into a background window

```bash
curl -X POST http://127.0.0.1:8745/api/window/post -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hwnd":12345,"action":"type","text":"Hello from background!"}'
```

### Check input routing first (recommended)

`/api/window/post` routes each request automatically, but you can (and
should, when debugging) ask how a window receives input **before** posting:

```bash
curl "http://127.0.0.1:8745/api/window/input-mode?hwnd=12345" \
  -H "X-Auth-Token: $TOKEN"
```

| Result | Meaning | What post does |
|---|---|---|
| `postmessage` | Classic Win32 app | Background PostMessage — no focus change |
| `focused` | Window already foreground | SendInput directly |
| `uia` | WinUI/UWP/XAML surface | **Focuses the window** + SendInput (focus is unavoidable — posted messages are swallowed by these apps) |
| `invalid` | Dead/unreachable window | HTTP 409 |

You can force a path with `"mode": "background"` or `"mode": "focused"`.

> ⚠️ **Important:** `uia` mode steals focus even though the endpoint is
> "background" control. If the user is actively typing on their desktop,
> warn them or wait — modern apps give you no other delivery path.

### Find child controls (for classic apps)

```bash
curl "http://127.0.0.1:8745/api/window/children?hwnd=12345" \
  -H "X-Auth-Token: $TOKEN"
```

Then target the child control's hwnd:

```bash
curl -X POST http://127.0.0.1:8745/api/window/post -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hwnd":CHILD_HWND,"action":"type","text":"typed to child control"}'
```

> Modern Notepad and other XAML-hosted apps have **no** classic child
> controls — `input-mode` reports `uia` and the focused path handles them
> automatically. Child targeting is only useful for classic Win32 apps.

### Send key to background window

```bash
curl -X POST http://127.0.0.1:8745/api/window/post -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hwnd":12345,"action":"key","key":"enter"}'

curl -X POST http://127.0.0.1:8745/api/window/post -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hwnd":12345,"action":"hotkey","keys":["ctrl","s"]}'
```

---

## Virtual Desktops

```bash
# List desktops
curl -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8745/api/desktops

# Switch to desktop 2
curl -X POST http://127.0.0.1:8745/api/desktop -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"switch","number":2}'

# Create a new desktop
curl -X POST http://127.0.0.1:8745/api/desktop -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"create"}'
```

**Rule:** The agent never switches desktops silently.  Move the target window
to a background desktop first, then work on it via focus-free control.

---

## Game Mode

Game mode locks the cursor to screen center and uses relative mouse movement
for camera look.

```bash
# Start
curl -X POST http://127.0.0.1:8745/api/game -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"start","sensitivity":12}'

# Camera look (dx=positive → right, dy=positive → down)
curl -X POST http://127.0.0.1:8745/api/game -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"move","dx":50,"dy":0}'

# Heartbeat (keep watchdog alive during long holds)
curl -X POST http://127.0.0.1:8745/api/game -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"heartbeat"}'

# Stop
curl -X POST http://127.0.0.1:8745/api/game -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"stop"}'
```

### Emergency stop

1. **Physical `Esc` / `Alt+Tab`** — always works (hardware input)
2. `POST /api/release_all` — instant release
3. **Watchdog** — automatic after 30 s of inactivity

```bash
curl -X POST http://127.0.0.1:8745/api/release_all -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{}'
```

---

## Vision Access for Image Models

For models that can consume images directly (GPT-4V, Claude Vision, Gemini):

### Single frame

```bash
# Full resolution JPEG
curl -H "X-Auth-Token: $TOKEN" -o frame.jpg http://127.0.0.1:8745/api/vision/frame

# Half-size greyscale as base64 (for text-only API)
curl "http://127.0.0.1:8745/api/vision/frame?scale=0.5&gray=1&format=base64" \
  -H "X-Auth-Token: $TOKEN"
```

### Live stream

```bash
# MJPEG stream at 10 FPS (continuous monitoring)
curl "http://127.0.0.1:8745/api/stream?fps=10&quality=70&scale=0.5" \
  -H "X-Auth-Token: $TOKEN"
```

### Region capture

```bash
# Only the bottom 100px (e.g., taskbar / hotbar area)
curl "http://127.0.0.1:8745/api/vision/frame?region=0,980,1920,100" \
  -H "X-Auth-Token: $TOKEN"
```

---

## Text-Based Motion Detection

For agents that **cannot** see images but need to know "what changed":

```bash
# First call: store the reference frame
curl -X POST http://127.0.0.1:8745/api/vision/diff -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{"grab":"gray"}'

# Subsequent calls: compare and get diff
curl -X POST http://127.0.0.1:8745/api/vision/diff -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{}'
```

Response:

```json
{
  "ok": true,
  "changed": true,
  "changed_pct": 12.5,
  "bbox": [100, 200, 400, 350],
  "tiles": [
    {"row": 2, "col": 4, "pct": 35.2, "center": [1000, 390]},
    {"row": 3, "col": 5, "pct": 18.7, "center": [1200, 570]}
  ]
}
```

- `tiles[].center` — clickable coordinates of each changed region
- `tiles[].pct` — percentage of pixels that changed in that tile
- `bbox` — overall bounding box of all changes

### Typical game loop (no vision)

```
1. POST /api/vision/diff {"grab":"gray"}   → store reference
2. POST /api/game {"action":"move",...}     → rotate camera
3. POST /api/vision/diff                    → what changed? where?
4. Decide next action based on change map
5. Repeat from step 2
```

---

## Bandwidth Optimization

| Approach | Payload | Use Case |
|---|---|---|
| `scale=1.0, gray=0` | ~500 KB | Full detail |
| `scale=0.5, gray=1` | ~50 KB | Good for most vision models |
| `scale=0.25, gray=1` | ~10 KB | Maximum compression |
| `diff` (text) | ~1 KB | Text-only agents |
| `region=...` | Variable | Focus on specific area |
| `quality=50` | Smaller JPEG | Acceptable quality loss |

**Recommended for LLM agents:** `scale=0.5&gray=1&quality=70` — good balance
of detail and token efficiency.

---

## Error Handling

| Code | Meaning | Action |
|---|---|---|
| 200 | Success | Continue |
| 400 | Bad request / missing params | Check request body |
| 401 | Unauthorized | Check X-Auth-Token header |
| 403 | Forbidden (blocked key/combo) | Use an alternative approach |
| 405 | Method not allowed | GET vs POST mismatch |
| 409 | Focus mismatch / window gone | Re-read windows, re-focus |
| 415 | Wrong Content-Type | Use `application/json` |
| 503 | OCR not installed | Install `rapidocr-onnxruntime` or skip OCR |

---

## Safety Guarantees

These are enforced at the API level and cannot be bypassed:

1. **Alt+F4 is blocked** — windows can only be closed via WM_CLOSE
2. **Win key is blocked** — no Start menu / task switching
3. **Delete is blocked** — no permanent deletion risk
4. **Focus guard** — typing to the wrong window is refused (409)
5. **Critical process protection** — winlogon, csrss, etc. cannot be killed
6. **Watchdog** — stuck input auto-releases after 30 s
7. **Auth token** — foreign pages cannot control the machine

---

## Complete curl Examples

### Full app automation sequence

```bash
TOKEN=$(cat .token)
H1="X-Auth-Token: $TOKEN"
H2="Content-Type: application/json"

# Find and focus Notepad
NOTEPAD=$(curl -s -H "$H1" http://127.0.0.1:8745/api/windows | \
  python -c "import sys,json; ws=[w for w in json.load(sys.stdin)['windows'] if 'notepad' in w['title'].lower()]; print(ws[0]['hwnd'] if ws else '')")

curl -X POST http://127.0.0.1:8745/api/window -H "$H1" -H "$H2" \
  -d "{\"hwnd\":$NOTEPAD,\"action\":\"focus\"}"

# Type with focus guard
curl -X POST http://127.0.0.1:8745/api/key -H "$H1" -H "$H2" \
  -d "{\"action\":\"type\",\"text\":\"Hello from agent!\",\"expect_hwnd\":$NOTEPAD}"

# Read what's on screen
curl -X POST http://127.0.0.1:8745/api/ocr -H "$H1" -H "$H2" -d '{}'

# Save the file
curl -X POST http://127.0.0.1:8745/api/key -H "$H1" -H "$H2" \
  -d "{\"action\":\"hotkey\",\"keys\":[\"ctrl\",\"s\"],\"expect_hwnd\":$NOTEPAD}"
```

### Full game session

```bash
TOKEN=$(cat .token)
H1="X-Auth-Token: $TOKEN"
H2="Content-Type: application/json"

# Focus Minecraft
MC=$(curl -s -H "$H1" http://127.0.0.1:8745/api/windows | \
  python -c "import sys,json; ws=[w for w in json.load(sys.stdin)['windows'] if 'minecraft' in w['title'].lower()]; print(ws[0]['hwnd'] if ws else '')")

curl -X POST http://127.0.0.1:8745/api/window -H "$H1" -H "$H2" \
  -d "{\"hwnd\":$MC,\"action\":\"focus\"}"

# Start game mode
curl -X POST http://127.0.0.1:8745/api/game -H "$H1" -H "$H2" \
  -d '{"action":"start","sensitivity":12}'

# Walk forward
curl -X POST http://127.0.0.1:8745/api/key -H "$H1" -H "$H2" \
  -d '{"action":"down","key":"w"}'

# Look right while walking
curl -X POST http://127.0.0.1:8745/api/game -H "$H1" -H "$H2" \
  -d '{"action":"move","dx":30,"dy":0}'

# Stop walking
curl -X POST http://127.0.0.1:8745/api/key -H "$H1" -H "$H2" \
  -d '{"action":"up","key":"w"}'

# Place block
curl -X POST http://127.0.0.1:8745/api/mouse -H "$H1" -H "$H2" \
  -d '{"action":"click","button":"right","x":960,"y":540}'

# Exit game mode
curl -X POST http://127.0.0.1:8745/api/game -H "$H1" -H "$H2" \
  -d '{"action":"stop"}'
```

---

## Important: Screenshot Cleanup

**Screenshots taken during task execution must be deleted after review.**

Screenshots are temporary artifacts used for visual verification only. They
should NOT be kept permanently on disk. After completing a task or ending a
session:

```bash
# Clean up all screenshots
rm -f /tmp/screen*.jpg /tmp/screen.jpg
```

This prevents:
- Disk space waste
- Privacy concerns (screenshots may contain sensitive information)
- Stale data accumulation

Agents should always clean up screenshots in their final step.
