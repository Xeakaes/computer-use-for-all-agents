# Screen Control — Development Roadmap

> A practical roadmap for hardening the current Windows implementation, introducing a platform-independent architecture, and adding Linux/macOS support without requiring local access to every operating system.

## Guiding Principles

- Keep `server.py` platform-agnostic.
- Put OS-specific behavior behind backend interfaces.
- Prefer explicit capabilities over pretending that every platform supports every feature.
- Fail closed when permissions, focus, or target validation are unavailable.
- Preserve the `READ → PLAN → ACT → VERIFY` agent workflow.
- Separate deterministic unit/API tests from real desktop smoke tests.
- Treat remote MCP exposure as a high-risk deployment mode.

---

# Phase 0 — Define Scope and Compatibility

## Goals

- Clearly define what the project is and is not.
- Establish supported operating systems and display systems.
- Avoid overpromising parity with commercial computer-use products.

## Tasks

- [ ] Add a README section: `What this project is / is not`.
- [ ] Describe the project as a model-independent perception and actuation layer.
- [ ] Explicitly state that the repository does not contain an AI model or autonomous reasoning engine.
- [ ] Define initial support targets:
  - [ ] Windows 10/11 — primary and most complete backend.
  - [ ] Linux X11 — first Linux target.
  - [ ] macOS — screen capture and input with explicit permissions.
  - [ ] Linux Wayland — capability-based, limited support initially.
- [ ] Add a platform support matrix to the documentation.
- [ ] Define which features are experimental, partial, or unsupported per platform.

## Suggested Support Matrix

| Capability | Windows | Linux X11 | Linux Wayland | macOS |
|---|---:|---:|---:|---:|
| Screen capture | Full | Full | Portal-dependent | Permission required |
| OCR | Full/optional | Full/optional | Full/optional | Full/optional |
| Mouse control | Full | Full | Restricted | Accessibility permission |
| Keyboard control | Full | Full | Restricted | Accessibility permission |
| Window enumeration | Full | WM-dependent | Limited | Accessibility/API-dependent |
| Background input | Strong | WM/app-dependent | Usually unavailable | Limited |
| Virtual desktops | Supported | DE/WM-dependent | DE/WM-dependent | Spaces-specific |
| Game mode | Supported | Experimental | Limited | Experimental |

---

# Phase 1 — Platform-Independent Architecture

## Goals

Move all OS-specific code behind a common backend interface while preserving the existing Windows behavior.

## Suggested Structure

```text
screen-control/
├── server.py
├── mcp_server.py
├── control.py                  # temporary compatibility layer or Windows adapter
├── core/
│   ├── capabilities.py
│   ├── errors.py
│   ├── models.py
│   └── policies.py
├── backends/
│   ├── __init__.py
│   ├── base.py
│   ├── windows.py
│   ├── linux.py
│   └── macos.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── platform/
│   └── fakes/
└── docs/
    ├── architecture.md
    ├── installation-windows.md
    ├── installation-linux.md
    ├── installation-macos.md
    ├── linux-x11.md
    ├── linux-wayland.md
    ├── macos-permissions.md
    ├── security.md
    └── troubleshooting.md
```

## Backend Interface

Create a common interface such as:

```python
class PlatformBackend(ABC):
    def capture_screen(self, monitor=1, region=None): ...
    def capture_window(self, window_id, client=False): ...
    def mouse_move(self, x, y, duration=0.15): ...
    def mouse_click(self, x, y, button="left", clicks=1): ...
    def mouse_scroll(self, clicks, x=None, y=None): ...
    def mouse_drag(self, x1, y1, x2, y2, duration=0.15): ...
    def key_press(self, key): ...
    def key_down(self, key): ...
    def key_up(self, key): ...
    def hotkey(self, keys): ...
    def type_text(self, text, interval=0.03): ...
    def list_windows(self): ...
    def focus_window(self, window_id): ...
    def close_window(self, window_id, **expectations): ...
    def get_active_window(self): ...
    def release_all(self): ...
    def get_capabilities(self): ...
```

## Tasks

- [ ] Introduce `PlatformBackend`.
- [ ] Move the current Windows implementation into `WindowsBackend`.
- [ ] Keep temporary compatibility wrappers so existing API routes do not break.
- [ ] Add backend discovery based on `sys.platform` and display server.
- [ ] Add dependency checks before backend initialization.
- [ ] Make backend selection overridable for tests.
- [ ] Ensure `server.py` never imports Windows-only APIs directly.
- [ ] Add architecture documentation and a backend lifecycle diagram.

---

# Phase 2 — Capabilities and Error Handling

## Goals

Allow agents and clients to understand what the current machine can actually do.

## Tasks

- [ ] Add `GET /api/capabilities`.
- [ ] Add `GET /api/permissions`.
- [ ] Include platform, OS version, architecture, display server, and backend name.
- [ ] Report capability status as `true`, `false`, or `unknown`.
- [ ] Add a startup health check.
- [ ] Print clear startup output:
  - `[OK] Screen capture`
  - `[OK] Mouse control`
  - `[WARN] Window enumeration unavailable`
  - `[ERROR] Accessibility permission missing`
- [ ] Never silently emulate unsupported behavior.
- [ ] Add remediation messages for missing permissions.

## Standard Error Codes

- `PERMISSION_REQUIRED`
- `UNSUPPORTED_PLATFORM`
- `UNSUPPORTED_DISPLAY_SERVER`
- `BACKEND_UNAVAILABLE`
- `WINDOW_NOT_FOUND`
- `FOCUS_MISMATCH`
- `CAPTURE_FAILED`
- `INPUT_BLOCKED`
- `INVALID_TARGET`
- `RESOURCE_LIMIT`
- `OPERATION_TIMEOUT`

## Standard Error Shape

```json
{
  "ok": false,
  "error": {
    "code": "PERMISSION_REQUIRED",
    "message": "Screen recording permission is required",
    "platform": "macos",
    "action": "screen_capture",
    "remediation": "Open System Settings > Privacy & Security > Screen Recording"
  }
}
```

---

# Phase 3 — Improve Windows Reliability

The Windows backend is currently the reference implementation. Stabilize it before adding multiple new platforms.

## Tasks

- [ ] Add regression tests for all existing security checks.
- [ ] Add multi-monitor tests.
- [ ] Add DPI tests for 100%, 125%, and 150% scaling.
- [ ] Test Turkish Q and English keyboard layouts.
- [ ] Test focus changes during input.
- [ ] Test window disappearance between discovery and action.
- [ ] Test stale `hwnd` and `pid` values.
- [ ] Test watchdog behavior after connection loss.
- [ ] Test emergency `release_all` under every held-input state.
- [ ] Add explicit timeout handling for slow OCR and window operations.
- [ ] Document administrator/elevated-window limitations.
- [ ] Add a Windows troubleshooting guide.

## High-Risk Operations

Review and possibly require explicit confirmation for:

- Process termination.
- Window killing.
- Destructive keyboard shortcuts.
- Remote MCP access.
- Game mode.
- Actions involving elevated applications.

---

# Phase 4 — Testing Architecture

## Test Layers

### Unit Tests

No real screen or input device required.

- [ ] Test validation.
- [ ] Test authentication.
- [ ] Test policy rules.
- [ ] Test coordinate parsing.
- [ ] Test capability reporting.
- [ ] Test error serialization.
- [ ] Test token and scoped-key lifecycle.

### Integration Tests

Use a fake backend and test the complete HTTP/MCP behavior.

- [ ] Create `FakeBackend`.
- [ ] Record all requested actions instead of sending real input.
- [ ] Verify that routes call the correct backend methods.
- [ ] Verify focus guards.
- [ ] Verify dangerous-key blocking.
- [ ] Verify watchdog state transitions.
- [ ] Verify MCP-to-REST mapping.

### Platform Smoke Tests

Run only on a real OS with a desktop session.

- [ ] Screen capture.
- [ ] OCR capture.
- [ ] Mouse movement.
- [ ] Keyboard input.
- [ ] Window discovery.
- [ ] Focus and verification.
- [ ] Release-all behavior.
- [ ] Permission reporting.

## Test Layout

```text
tests/
├── unit/
├── integration/
├── platform/
│   ├── test_windows.py
│   ├── test_linux_x11.py
│   └── test_macos.py
├── fakes/
│   └── fake_backend.py
└── fixtures/
```

## CI Matrix

```yaml
strategy:
  matrix:
    os:
      - ubuntu-24.04
      - macos-15
      - windows-2022

runs-on: ${{ matrix.os }}
```

Use CI for:

- Import tests.
- Dependency installation.
- Unit tests.
- Fake-backend integration tests.
- MCP startup tests.
- Static checks.

Do not assume hosted CI provides a fully interactive desktop with all permissions. Real GUI smoke tests require separate runners or manual/device testing.

---

# Phase 5 — Linux X11 Backend

## Initial Target

Start with Ubuntu 24.04 using an X11 session.

Check the display server:

```bash
echo "$XDG_SESSION_TYPE"
```

Expected first target:

```text
x11
```

## Useful Dependencies

```bash
sudo apt update
sudo apt install -y \
  python3-tk \
  python3-dev \
  python3-xlib \
  scrot \
  xdotool \
  wmctrl
```

Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Suggested Tools

| Function | Candidate technology |
|---|---|
| Screen capture | `mss`, Pillow |
| Mouse/keyboard | PyAutoGUI, Xlib, xdotool |
| Window listing | `wmctrl`, Xlib, xdotool |
| Active window | `xdotool getactivewindow` |
| Window metadata | `xprop`, `xwininfo` |
| OCR | RapidOCR |

## Tasks

- [ ] Implement `LinuxX11Backend`.
- [ ] Add screen capture.
- [ ] Add absolute mouse control.
- [ ] Add keyboard input.
- [ ] Add window listing.
- [ ] Add active-window detection.
- [ ] Add focus control.
- [ ] Add X11 permission/environment diagnostics.
- [ ] Add Ubuntu X11 smoke tests.
- [ ] Document limitations for different window managers.
- [ ] Test GNOME, KDE, and XFCE where possible.

## Important Limitations

- PyAutoGUI may have primary-monitor limitations.
- Window behavior depends on the window manager.
- Background input is not uniformly reliable.
- Coordinates may differ under fractional scaling.
- X11 security is weaker than Wayland; document the trade-off.

---

# Phase 6 — Linux Wayland Support

Treat Wayland as a separate backend/feature set rather than as a drop-in replacement for X11.

## Relevant Technologies

- `xdg-desktop-portal`
- ScreenCast portal
- RemoteDesktop portal
- PipeWire
- Desktop-environment-specific APIs

## Tasks

- [ ] Detect Wayland using `XDG_SESSION_TYPE`.
- [ ] Report Wayland capabilities explicitly.
- [ ] Implement portal-based screen capture where practical.
- [ ] Investigate RemoteDesktop portal for input.
- [ ] Avoid claiming universal global input support.
- [ ] Return `UNSUPPORTED_DISPLAY_SERVER` when an operation cannot be safely performed.
- [ ] Test GNOME Wayland and KDE Wayland independently.
- [ ] Document permission prompts and user approval flow.

## Recommended Initial Policy

```text
Wayland screen capture: experimental/portal-dependent
Global mouse injection: unsupported unless verified
Global keyboard injection: unsupported unless verified
Window management: desktop-environment-dependent
```

---

# Phase 7 — macOS Backend

## Required Permissions

### Screen Recording

```text
System Settings
→ Privacy & Security
→ Screen Recording
```

### Accessibility

```text
System Settings
→ Privacy & Security
→ Accessibility
```

The application that actually performs the operation may need permission. This could be Terminal, Python, an IDE, or a packaged application.

## Suggested Technologies

| Function | Candidate technology |
|---|---|
| Screen capture | Quartz/CoreGraphics, ScreenCaptureKit, `screencapture` |
| Mouse/keyboard | `CGEvent`, PyObjC, Accessibility API |
| Window listing | `CGWindowList`, Accessibility API |
| Focus | Accessibility API |
| OCR | RapidOCR |
| Python bridge | PyObjC |

## Initial Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pyautogui mss pillow pyobjc-framework-Quartz
```

Basic screen-capture smoke test:

```bash
screencapture -x screen.png
```

## Tasks

- [ ] Implement `MacOSBackend`.
- [ ] Add screen-capture permission detection.
- [ ] Add Accessibility permission detection.
- [ ] Add screenshot capture.
- [ ] Add mouse input through Quartz/CoreGraphics.
- [ ] Add keyboard input through `CGEvent`.
- [ ] Add active-window detection.
- [ ] Add window listing.
- [ ] Add focus control where permitted.
- [ ] Add Retina coordinate normalization.
- [ ] Test Intel and Apple Silicon if possible.
- [ ] Document Secure Input and privileged-app limitations.
- [ ] Add macOS-specific troubleshooting.

## macOS-Specific Risks

- TCC permissions may be denied or reset.
- Retina pixels and logical coordinates may differ.
- Secure Input can block keyboard observation/injection.
- Some applications do not expose complete Accessibility information.
- Sandboxing, code signing, and application identity affect permissions.
- Screen Recording permissions may require application restart.

---

# Phase 8 — Agent and MCP Improvements

## Tasks

- [ ] Make `get_info` expose platform and capabilities.
- [ ] Make MCP tool descriptions mention platform limitations.
- [ ] Prevent the agent from calling unsupported tools.
- [ ] Return structured remediation instructions.
- [ ] Add tool-level risk metadata.
- [ ] Add optional confirmation hooks for destructive operations.
- [ ] Add explicit target-window binding to more operations.
- [ ] Extend `expect_hwnd`-style guards to window posting and focus-sensitive actions.
- [ ] Add operation IDs for tracing and audit logs.
- [ ] Add clear “action sent” versus “action verified” status.

## Recommended Agent Contract

```text
1. Inspect capabilities.
2. Discover the target window.
3. Read the screen or OCR.
4. Select one action.
5. Verify the target still exists and focus is correct.
6. Execute one action.
7. Re-read the screen.
8. Continue only if the result is consistent with expectations.
```

---

# Phase 9 — Policy, Audit, and Deployment Safety

## Policy System

Consider a configurable policy file:

```json
{
  "allow": [
    "screenshot",
    "ocr",
    "mouse.click",
    "keyboard.type"
  ],
  "deny": [
    "window.kill",
    "process.terminate"
  ],
  "confirm": [
    "window.close",
    "keyboard.hotkey"
  ]
}
```

## Tasks

- [ ] Add allow/deny/confirm policy evaluation.
- [ ] Add per-client or per-scoped-key permissions.
- [ ] Add audit logging without storing sensitive screen data by default.
- [ ] Add request IDs and timestamps.
- [ ] Add rate limits for remote transports.
- [ ] Add configurable maximum action duration.
- [ ] Add remote-MCP deployment warnings.
- [ ] Make short-lived scoped keys the recommended remote credential.
- [ ] Ensure the master token is never printed into URLs.
- [ ] Add an explicit shutdown/revoke command.

---

# Phase 10 — Documentation and Release Quality

## Documentation Tasks

- [ ] Rewrite README quick start for a five-minute first success.
- [ ] Add Windows installation guide.
- [ ] Add Linux X11 guide.
- [ ] Add Linux Wayland limitations guide.
- [ ] Add macOS permissions guide.
- [ ] Add architecture diagram.
- [ ] Add threat model.
- [ ] Add API examples in PowerShell, Bash, and Python.
- [ ] Add MCP setup examples for Claude Desktop, Cursor, and VS Code.
- [ ] Add troubleshooting for OCR, DPI, permissions, and display servers.
- [ ] Add a known limitations section.
- [ ] Add a compatibility table.
- [ ] Add contribution guidelines for platform maintainers.

## Release Tasks

- [ ] Pin or bound dependencies carefully.
- [ ] Add versioned release notes.
- [ ] Add a changelog.
- [ ] Add automated security tests to CI.
- [ ] Add static typing where practical.
- [ ] Add linting and formatting checks.
- [ ] Add a minimal packaging/installer story.
- [ ] Test fresh installation on all supported platforms.
- [ ] Publish a small demo video or GIF for each major backend.

---

# Recommended Immediate Sprint

The most useful next sprint would be:

1. [ ] Create `PlatformBackend`.
2. [ ] Extract the current Windows implementation into `WindowsBackend`.
3. [ ] Add `FakeBackend`.
4. [ ] Move API tests to the fake backend.
5. [ ] Add `/api/capabilities`.
6. [ ] Add standardized errors.
7. [ ] Add GitHub Actions for Ubuntu, macOS, and Windows unit/integration tests.
8. [ ] Add Linux X11 installation documentation.
9. [ ] Add macOS permission documentation.
10. [ ] Add explicit support/limitation tables.

This sprint creates the foundation for multi-platform work without destabilizing the existing Windows implementation.

---

# Useful References

## Cross-Platform Automation

- [PyAutoGUI documentation](https://pyautogui.readthedocs.io/en/latest/)
- [PyAutoGUI GitHub](https://github.com/asweigart/pyautogui)
- [MSS documentation](https://python-mss.readthedocs.io/)
- [Pillow documentation](https://pillow.readthedocs.io/)

## Linux

- [xdotool](https://www.semicomplete.com/projects/xdotool/)
- [wmctrl manual](https://manpages.ubuntu.com/manpages/jammy/man1/wmctrl.1.html)
- [python-xlib](https://github.com/python-xlib/python-xlib)
- [XDG Desktop Portal](https://flatpak.github.io/xdg-desktop-portal/)
- [ScreenCast Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
- [RemoteDesktop Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
- [PipeWire](https://pipewire.org/)

## macOS

- [Apple ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit)
- [Apple Quartz Window Services](https://developer.apple.com/documentation/coregraphics/quartz_window_services)
- [Apple CGEvent](https://developer.apple.com/documentation/coregraphics/cgevent)
- [Apple Accessibility API](https://developer.apple.com/documentation/applicationservices/axui_element)
- [PyObjC documentation](https://pyobjc.readthedocs.io/)
- [PyObjC GitHub](https://github.com/ronaldoussoren/pyobjc)

## CI and Runners

- [GitHub-hosted runners](https://docs.github.com/actions/using-github-hosted-runners/about-github-hosted-runners)
- [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [GitHub runner images](https://github.com/actions/runner-images)
- [Self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/use-in-a-workflow)

---

# Final Architecture Goal

```text
                    AI Agent / MCP Client
                              │
                              ▼
                     server.py / MCP layer
                              │
              auth · policy · validation · audit
                              │
                              ▼
                    PlatformBackend interface
                 ┌────────────┼────────────┐
                 ▼            ▼            ▼
          WindowsBackend  LinuxBackend  MacOSBackend
                 │            │            │
             Win32 API    X11/Wayland   Quartz/Accessibility
                              │
                              ▼
                     capability-aware behavior
```

The goal is not to make every operating system behave identically. The goal is to provide a consistent API where supported, transparent capability reporting where partially supported, and safe refusal where an operation cannot be performed reliably.
