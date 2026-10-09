"""LinuxBackend: display detection, Wayland fail-fast, xdotool input,
wmctrl/xwininfo window management, uinput game mode.

Unit tests patch os.environ and mock the xdotool runner so no display is
required; the TestLinuxLiveInput, TestLinuxLiveWindows,
TestLinuxLiveCapture and TestLinuxLiveGame groups run only against a
real X11 DISPLAY.
"""
import os
import re
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from backends import linux as linux_mod
from backends.linux import (KEYMAP, LinuxBackend, held_state, key_down,
                            key_hotkey, key_press, key_up, mouse_click,
                            mouse_down, mouse_drag, mouse_move,
                            mouse_move_relative, mouse_scroll, mouse_up,
                            release_all, run_x11, type_segments, type_text,
                            xdotool_key)
from backends.uinput import (UInputDevice, key_to_code,
                             uinput_permission_error)
from core.errors import ApiError

SPEC_CAPABILITIES = {
    "platform": "linux", "screen_capture": True, "ocr": "optional",
    "mouse_control": True, "keyboard_control": True,
    "window_enumeration": True, "background_input": False,
    "virtual_desktops": False, "game_mode": True,
}


class TestLinuxBackend(unittest.TestCase):
    def test_capabilities_match_spec(self):
        with mock.patch.dict(os.environ,
                             {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            caps = LinuxBackend().get_capabilities()
        self.assertEqual(caps, SPEC_CAPABILITIES)

    def test_wayland_raises_unsupported_display_server(self):
        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland",
                                          "WAYLAND_DISPLAY": "wayland-0"}):
            with self.assertRaises(ApiError) as ctx:
                LinuxBackend()
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_DISPLAY_SERVER")
        self.assertEqual(ctx.exception.status, 501)

    def test_x11_session_constructs(self):
        with mock.patch.dict(os.environ,
                             {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            backend = LinuxBackend()
            caps = backend.get_capabilities()
        self.assertEqual(caps, SPEC_CAPABILITIES)

    def test_no_display_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            backend = LinuxBackend()
            for call in (lambda: backend.mouse_move(1, 1),
                         lambda: backend.key_press("a"),
                         lambda: backend.list_windows(),
                         lambda: backend.screenshot()):
                with self.assertRaises(ApiError) as ctx:
                    call()
                self.assertEqual(ctx.exception.code, "BACKEND_UNAVAILABLE")
                self.assertEqual(ctx.exception.status, 501)

    def test_read_only_getters_safe_without_display(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            backend = LinuxBackend()
            self.assertEqual(backend.held_state(),
                             {"keys": [], "buttons": []})
            self.assertEqual(backend.release_all(),
                             {"ok": True, "released": []})
            self.assertFalse(backend.game_active())
            self.assertEqual(backend.probe_input_mode(1), "invalid")

    def test_game_active_is_plain_flag(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            backend = LinuxBackend()
            self.assertIs(backend.game_active(), False)

    def test_game_stop_never_raises_without_display_or_device(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            backend = LinuxBackend()
            self.assertEqual(backend.game_stop(),
                             {"ok": True, "released": []})
            self.assertIs(backend.game_active(), False)

    def test_forbidden_policy(self):
        with mock.patch.dict(os.environ,
                             {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            backend = LinuxBackend()
        self.assertIsNone(backend.assert_allowed(["a"]))
        with self.assertRaises(PermissionError):
            backend.assert_allowed(["alt", "f4"])

    def test_assert_allowed_refuses_chord_string_and_super_alias(self):
        with mock.patch.dict(os.environ,
                             {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            backend = LinuxBackend()
        with self.assertRaises(PermissionError):
            backend.assert_allowed(["alt+f4"])
        with self.assertRaises(PermissionError):
            backend.assert_allowed(["super"])

    def test_stub_backends_is_macos_only(self):
        path = os.path.join(os.path.dirname(__file__),
                            "test_stub_backends.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("LinuxBackend", source)


class TestLinuxInputUnit(unittest.TestCase):
    """Task 6 unit contracts: KEYMAP, run_x11, held state, type_segments."""

    def test_keymap_enter_escape_arrows(self):
        self.assertEqual(xdotool_key("enter"), "Return")
        self.assertEqual(xdotool_key("esc"), "Escape")
        self.assertEqual(xdotool_key("up"), "Up")
        self.assertEqual(xdotool_key("a"), "a")
        self.assertEqual(xdotool_key("ctrl"), "ctrl")
        for n in range(1, 13):
            self.assertEqual(xdotool_key(f"f{n}"), f"F{n}")

    def test_every_keymap_name_has_a_uinput_code(self):
        """In-game (uinput) and out-of-game (xdotool) name parity."""
        for name in KEYMAP:
            with self.subTest(name=name):
                self.assertIsInstance(key_to_code(name), int)
        self.assertEqual(key_to_code("escape"), 1)
        self.assertEqual(key_to_code("home"), 102)
        self.assertEqual(key_to_code("end"), 107)

    def test_runner_raises_operation_timeout_on_hang(self):
        with self.assertRaises(ApiError) as ctx:
            run_x11(["sleep", "10"], timeout=0.2)
        self.assertEqual(ctx.exception.code, "OPERATION_TIMEOUT")
        self.assertEqual(ctx.exception.status, 504)

    def test_runner_missing_binary(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            with self.assertRaises(ApiError) as ctx:
                run_x11(["xdotool", "--version"])
        self.assertEqual(ctx.exception.code, "BACKEND_UNAVAILABLE")
        self.assertIn("xdotool", ctx.exception.remediation)

    def test_runner_nonzero_exit_raises_runtime_error(self):
        with self.assertRaises(RuntimeError):
            run_x11(["false"])

    def test_held_state_never_raises_without_xdotool(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(held_state(), {"keys": [], "buttons": []})
            self.assertEqual(release_all(), {"ok": True, "released": []})

    def test_type_text_splits_newlines(self):
        self.assertEqual(type_segments("a\nb"), ["a", "b"])


class TestLinuxInputContracts(unittest.TestCase):
    """Exact xdotool invocation contracts, with the runner mocked out."""

    def setUp(self):
        patcher = mock.patch("backends.linux.run_x11")
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.run.return_value = "X=10\nY=10\nSCREEN=0\nWINDOW=1\n"
        self.addCleanup(linux_mod._held_keys.clear)
        self.addCleanup(linux_mod._held_buttons.clear)

    def calls(self):
        return [c.args[0] for c in self.run.call_args_list]

    def test_mouse_move_absolute_and_duration_steps(self):
        mouse_move(100, 100, duration=0)
        self.assertEqual(self.calls(), [
            ["xdotool", "getmouselocation", "--shell"],
            ["xdotool", "mousemove", "--sync", "100", "100"],
        ])

        self.run.reset_mock()
        mouse_move(100, 100, duration=0.15)
        calls = self.calls()
        self.assertEqual(calls[0], ["xdotool", "getmouselocation", "--shell"])
        relative = [c for c in calls if c[1] == "mousemove_relative"]
        self.assertLessEqual(len(relative), 20)
        for c in relative:
            self.assertEqual(c[:4], ["xdotool", "mousemove_relative",
                                     "--sync", "--"])
        self.assertEqual(calls[-1],
                         ["xdotool", "mousemove", "--sync", "100", "100"])

        self.run.reset_mock()
        self.run.return_value = "X=100\nY=100\nSCREEN=0\nWINDOW=1\n"
        mouse_move(100, 100)
        self.assertEqual(self.calls(),
                         [["xdotool", "getmouselocation", "--shell"]])

    def test_mouse_move_never_issues_zero_delta_sync(self):
        proof_cases = [
            ((100, 100), (99, 99), 0.15),
            ((100, 100), (99, 99), 0.3),
            ((105, 100), (104, 100), 0.15),
            ((105, 100), (104, 100), 0.3),
            ((100, 105), (100, 104), 0.15),
            ((100, 105), (100, 104), 0.3),
            ((500, 500), (495, 495), 0.15),
            ((500, 500), (495, 495), 0.3),
            ((100, 100), (101, 101), 0.15),
            ((105, 100), (104, 101), 0.15),
            ((100, 100), (500, 500), 0.15),
            ((100, 100), (99, 99), 0),
            ((100, 100), (100, 100), 0.15),
        ]
        for start, target, duration in proof_cases:
            with self.subTest(start=start, target=target, duration=duration):
                self.run.reset_mock()
                self.run.return_value = (f"X={start[0]}\nY={start[1]}\n"
                                         "SCREEN=0\nWINDOW=1\n")
                mouse_move(target[0], target[1], duration=duration)
                x, y = start
                for c in self.calls():
                    if c[1] == "mousemove_relative":
                        x += int(c[4])
                        y += int(c[5])
                    elif c[1] == "mousemove":
                        new_x, new_y = int(c[3]), int(c[4])
                        self.assertNotEqual(
                            (new_x - x, new_y - y), (0, 0),
                            "zero-delta mousemove --sync blocks ~15s")
                        x, y = new_x, new_y
                self.assertEqual((x, y), target,
                                 "pointer must end exactly at the target")

    def test_mouse_move_relative_negative_arguments(self):
        mouse_move_relative(-5, -7)
        self.run.assert_called_once_with(
            ["xdotool", "mousemove_relative", "--sync", "--", "-5", "-7"])

    def test_mouse_click_and_scroll_button_mapping(self):
        mouse_click(5, 6, button="right", clicks=2)
        calls = self.calls()
        self.assertEqual(calls[0], ["xdotool", "getmouselocation", "--shell"])
        self.assertEqual(calls[1],
                         ["xdotool", "mousemove", "--sync", "5", "6"])
        self.assertEqual(calls[-1],
                         ["xdotool", "click", "--repeat", "2", "3"])

        self.run.reset_mock()
        mouse_scroll(3, x=7, y=8)
        calls = self.calls()
        self.assertEqual(calls[1],
                         ["xdotool", "mousemove", "--sync", "7", "8"])
        self.assertEqual(calls[-1],
                         ["xdotool", "click", "--repeat", "3", "4"])

        self.run.reset_mock()
        mouse_scroll(-2)
        self.run.assert_called_once_with(
            ["xdotool", "click", "--repeat", "2", "5"])

    def test_mouse_drag_press_move_release_sequence(self):
        mouse_drag(5, 5, 50, 60, duration=0.3, button="left")
        calls = self.calls()
        self.assertEqual(calls[0], ["xdotool", "getmouselocation", "--shell"])
        self.assertEqual(calls[1],
                         ["xdotool", "mousemove", "--sync", "5", "5"])
        self.assertEqual(calls[2], ["xdotool", "mousedown", "1"])
        self.assertIn(["xdotool", "getmouselocation", "--shell"], calls[3:])
        self.assertEqual(calls[-2],
                         ["xdotool", "mousemove", "--sync", "50", "60"])
        self.assertEqual(calls[-1], ["xdotool", "mouseup", "1"])

    def test_key_press_down_up_hotkey_invocations(self):
        key_press("enter")
        key_down("ctrl")
        key_up("ctrl")
        key_hotkey("ctrl", "c")
        self.assertEqual(self.calls(), [
            ["xdotool", "key", "Return"],
            ["xdotool", "keydown", "ctrl"],
            ["xdotool", "keyup", "ctrl"],
            ["xdotool", "key", "ctrl+c"],
        ])

    def test_key_hotkey_blocked_before_any_invocation(self):
        with self.assertRaises(PermissionError):
            key_hotkey("alt", "f4")
        self.run.assert_not_called()

    def test_key_press_chord_string_refused_before_any_invocation(self):
        with self.assertRaises(ValueError) as ctx:
            key_press("alt+f4")
        self.assertIn("chord", str(ctx.exception))
        self.assertIn("hotkey", str(ctx.exception))
        self.run.assert_not_called()

    def test_key_press_super_refused_as_win(self):
        with self.assertRaises(PermissionError):
            key_press("super")
        self.run.assert_not_called()

    def test_key_press_delete_refused_before_any_invocation(self):
        with self.assertRaises(PermissionError):
            key_press("delete")
        self.run.assert_not_called()

    def test_key_press_lone_plus_is_a_key_name(self):
        key_press("+")
        self.assertEqual(self.calls(), [["xdotool", "key", "+"]])

    def test_key_hotkey_super_l_refused_before_any_invocation(self):
        with self.assertRaises(PermissionError):
            key_hotkey("super", "l")
        self.run.assert_not_called()

    def test_key_hotkey_single_chord_string_refused(self):
        with self.assertRaises(PermissionError):
            key_hotkey(*["alt+f4"])
        self.run.assert_not_called()

    def test_key_hotkey_super_chord_refused_before_any_invocation(self):
        with self.assertRaises(PermissionError):
            key_hotkey("super", "page_up")
        self.run.assert_not_called()

    def test_type_text_one_type_call_per_segment(self):
        type_text("a\nb")
        self.assertEqual(self.calls(), [
            ["xdotool", "type", "--delay", "30", "--", "a"],
            ["xdotool", "key", "Return"],
            ["xdotool", "type", "--delay", "30", "--", "b"],
        ])

    def test_held_tracking_and_release_all(self):
        key_down("ctrl")
        mouse_down("left")
        self.assertEqual(held_state(), {"keys": ["ctrl"],
                                        "buttons": ["left"]})
        result = release_all()
        self.assertEqual(result,
                         {"ok": True, "released": ["ctrl", "mouse:left"]})
        calls = self.calls()
        self.assertIn(["xdotool", "keyup", "ctrl"], calls)
        self.assertIn(["xdotool", "mouseup", "1"], calls)
        self.assertEqual(held_state(), {"keys": [], "buttons": []})

    def test_key_up_discards_held_key(self):
        key_down("ctrl")
        key_up("ctrl")
        self.assertEqual(held_state(), {"keys": [], "buttons": []})
        self.assertEqual(release_all(), {"ok": True, "released": []})

    def test_release_all_reports_failures_without_raising(self):
        key_down("ctrl")
        self.run.side_effect = RuntimeError("boom")
        result = release_all()
        self.assertEqual(result,
                         {"ok": True,
                          "released": ["ctrl (release failed: boom)"]})
        self.assertEqual(held_state(), {"keys": [], "buttons": []})


class TestLinuxGameMode(unittest.TestCase):
    """Task 9 game mode: uinput routing, release, watchdog contracts."""

    def setUp(self):
        env = mock.patch.dict(os.environ,
                              {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
        env.start()
        self.addCleanup(env.stop)
        patcher = mock.patch("backends.linux.run_x11")
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(linux_mod._held_keys.clear)
        self.addCleanup(linux_mod._held_buttons.clear)
        self.addCleanup(self._close_leftover_device)
        self.backend = LinuxBackend()

    def _close_leftover_device(self):
        device = linux_mod._game_device
        linux_mod._game_device = None
        if device is not None:
            device.close()

    def test_release_all_releases_both_sources(self):
        device = mock.Mock()
        with mock.patch.object(linux_mod, "_game_device", device):
            linux_mod._hold("w", "x11", linux_mod._held_keys)
            linux_mod._hold("a", "uinput", linux_mod._held_keys)
            result = release_all()
        self.assertEqual(result, {"ok": True, "released": ["w", "a"]})
        self.assertEqual([c.args[0] for c in self.run.call_args_list],
                         [["xdotool", "keyup", "w"]])
        device.key_up.assert_called_once_with("a")
        self.assertEqual(linux_mod._held_keys, {})
        self.assertEqual(linux_mod._held_buttons, {})

    def test_game_move_applies_sensitivity(self):
        device = mock.Mock()
        with mock.patch.object(linux_mod, "_game_device", device):
            result = self.backend.game_move(3, -2, sensitivity=10)
        self.assertEqual(result, {"ok": True})
        device.move_rel.assert_called_once_with(30, -20)

    def test_game_move_without_device_is_a_noop(self):
        self.assertEqual(self.backend.game_move(3, -2), {"ok": True})

    def test_game_start_releases_first(self):
        events = []
        device = mock.Mock()

        def fake_release():
            events.append("release")
            return {"ok": True, "released": []}

        with mock.patch("backends.linux.release_all",
                        side_effect=fake_release), \
             mock.patch("backends.linux.UInputDevice",
                        side_effect=lambda: events.append("open") or device), \
             mock.patch.object(LinuxBackend, "screen_size",
                               return_value=(1920, 1080)):
            result = self.backend.game_start(sensitivity=7)
        self.assertEqual(events, ["release", "open"])
        self.assertEqual(result, {
            "ok": True, "center": [960, 540], "sensitivity": 7,
            "note": "cursor locked to center; use /api/game/move for "
                    "camera look; X11 cannot clip the cursor — the game "
                    "must capture the pointer",
        })
        self.assertTrue(self.backend.game_active())

    def test_game_start_failure_leaves_mode_inactive(self):
        with mock.patch.object(LinuxBackend, "screen_size",
                               return_value=(1920, 1080)), \
             mock.patch("backends.linux.UInputDevice",
                        side_effect=uinput_permission_error("game_start")):
            with self.assertRaises(ApiError) as ctx:
                self.backend.game_start()
        self.assertEqual(ctx.exception.code, "PERMISSION_REQUIRED")
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("input group", ctx.exception.remediation)
        self.assertFalse(self.backend.game_active())

    def test_uinput_open_failure_surfaces_permission_required(self):
        fake = mock.Mock()
        fake.ecodes = mock.Mock(EV_KEY=1, EV_REL=2, REL_X=0, REL_Y=1)
        fake.UInput.side_effect = PermissionError(13, "Permission denied")
        with mock.patch.dict(sys.modules, {"evdev": fake}):
            with self.assertRaises(ApiError) as ctx:
                UInputDevice()
        err = ctx.exception
        self.assertEqual(err.code, "PERMISSION_REQUIRED")
        self.assertEqual(err.status, 403)
        self.assertEqual(err.action, "game_start")
        self.assertEqual(err.remediation,
                         uinput_permission_error("game_start").remediation)
        self.assertIn("input group", err.remediation)

    def test_game_mode_routes_key_and_button_events(self):
        device = mock.Mock()
        with mock.patch.object(linux_mod, "_game_device", device):
            key_down("w")
            self.assertEqual(held_state(), {"keys": ["w"], "buttons": []})
            key_up("w")
            key_press("a")
            mouse_down("left")
            self.assertEqual(held_state(), {"keys": [], "buttons": ["left"]})
            mouse_up("left")
        device.key_down.assert_has_calls([mock.call("w"), mock.call("a")])
        device.key_up.assert_has_calls([mock.call("w"), mock.call("a")])
        device.button_down.assert_called_once_with("left")
        device.button_up.assert_called_once_with("left")
        for call in self.run.call_args_list:
            self.assertNotIn(call.args[0][1], ("key", "keydown", "keyup",
                                               "mousedown", "mouseup"))
        self.assertEqual(held_state(), {"keys": [], "buttons": []})

    def test_game_mode_key_routing_enforces_forbidden_policy(self):
        device = mock.Mock()
        with mock.patch.object(linux_mod, "_game_device", device):
            with self.assertRaises(ValueError):
                key_down("alt+f4")
            with self.assertRaises(PermissionError):
                key_press("super")
            with self.assertRaises(PermissionError):
                key_up("delete")
            with self.assertRaises(ValueError):
                mouse_down("bogus")
        self.assertEqual(device.mock_calls, [])
        self.assertEqual(linux_mod._held_keys, {})
        self.assertEqual(linux_mod._held_buttons, {})

    def test_game_stop_releases_closes_and_clears(self):
        device = mock.Mock()
        linux_mod._game_device = device
        linux_mod._hold("a", "uinput", linux_mod._held_keys)
        result = self.backend.game_stop()
        self.assertEqual(result, {"ok": True, "released": ["a"]})
        device.key_up.assert_called_once_with("a")
        device.close.assert_called_once_with()
        self.assertFalse(self.backend.game_active())
        self.assertEqual(linux_mod._held_keys, {})

    def test_game_stop_never_raises_when_releases_fail(self):
        device = mock.Mock()
        device.key_up.side_effect = OSError("uinput write failed")
        linux_mod._game_device = device
        linux_mod._hold("a", "uinput", linux_mod._held_keys)
        linux_mod._hold("w", "x11", linux_mod._held_keys)
        self.run.side_effect = RuntimeError("xdotool exploded")
        result = self.backend.game_stop()
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["released"]), 2)
        for entry in result["released"]:
            self.assertIn("release failed", entry)
        self.assertFalse(self.backend.game_active())
        self.assertEqual(linux_mod._held_keys, {})
        device.close.assert_called_once_with()


@unittest.skipUnless(os.environ.get("DISPLAY"), "requires an X11 DISPLAY")
class TestLinuxLiveInput(unittest.TestCase):
    """Live xdotool invocations against the real display (brief Step 1).

    This desktop is shared with interactive clients (games grab/warp the
    pointer, users steal focus), so the tests retry once and skip only when
    an external X client blocks --sync confirmation or steals focus.
    """

    def test_mouse_move_reaches_screen(self):
        coords = None
        for _ in range(3):
            try:
                mouse_move(100, 100)
            except ApiError as exc:
                if exc.code != "OPERATION_TIMEOUT":
                    raise
                time.sleep(0.5)
                continue
            out = run_x11(["xdotool", "getmouselocation", "--shell"])
            coords = dict(line.split("=", 1) for line in out.splitlines()
                          if "=" in line)
            if (abs(int(coords["X"]) - 100) <= 3
                    and abs(int(coords["Y"]) - 100) <= 3):
                break
            time.sleep(0.3)
        if coords is None:
            self.skipTest("xdotool mousemove --sync blocked by another "
                          "X client for every attempt")
        self.assertAlmostEqual(int(coords["X"]), 100, delta=3)
        self.assertAlmostEqual(int(coords["Y"]), 100, delta=3)

    def test_type_text_into_xev(self):
        proc = subprocess.Popen(["xev"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        try:
            wid = run_x11(["xdotool", "search", "--sync", "--name",
                           "Event Tester"]).split()[-1]
            run_x11(["xdotool", "windowfocus", "--sync", wid])
            time.sleep(0.2)
            active = run_x11(["xdotool", "getactivewindow"]).strip()
            if active != wid:
                self.skipTest("focus stolen by another X client before "
                              "typing; refusing to type into it")
            type_text("hi")
            time.sleep(0.3)
            active = run_x11(["xdotool", "getactivewindow"]).strip()
            if active != wid:
                self.skipTest("focus stolen by another X client while "
                              "typing; key events unreliable")
        finally:
            proc.terminate()
            out, _ = proc.communicate(timeout=10)
        self.assertIn("keysym 0x68, h", out)
        self.assertIn("keysym 0x69, i", out)

    def test_key_hotkey_blocked(self):
        with self.assertRaises(PermissionError):
            key_hotkey("alt", "f4")


class TestLinuxWindowParsers(unittest.TestCase):
    """Task 7 fixture tests: wmctrl/xwininfo parsing (pure, CI-safe)."""

    # Real captured `wmctrl -lGpx` line: every column space-separated, the
    # client-machine column always present, and a UTF-8 title with spaces.
    WMCTRL_LINE = (
        "0x05800006  0 52465  0    80   1920 1040 discord.discord       "
        "xeakaes-Cyborg-15-A13VF #🤖bot-kanalı | KAKA'NIN DÖNERCİSİ - Discord"
    )

    # Real captured `xwininfo -root -tree` fragment (header lines, a named
    # window with WM_CLASS, and an unnamed child with no class).
    XWININFO_TREE = (
        'xwininfo: Window id: 0x637 (the root window) (has no name)\n'
        '\n'
        '  Root window id: 0x637 (the root window) (has no name)\n'
        '  Parent window id: 0x0 (none)\n'
        '     125 children:\n'
        '     0x380003e "Masaüstü": ("nemo-desktop" "Nemo-desktop")  '
        '1920x1040+0+40  +0+40\n'
        '        1 child:\n'
        '        0x380003f (has no name): ()  1x1+-1+-1  +0+39\n'
        '     0x360002a "xmessage": ("xmessage" "Xmessage")  40x5+10+40  '
        '+50+82\n'
    )

    def test_parse_wmctrl_line(self):
        self.assertEqual(linux_mod.parse_wmctrl(self.WMCTRL_LINE), {
            "hwnd": 0x05800006,
            "desktop": 0,
            "rect": [0, 80, 1920, 1120],
            "pid": 52465,
            "wm_class": "discord.discord",
            "title": "#🤖bot-kanalı | KAKA'NIN DÖNERCİSİ - Discord",
        })

    def test_parse_wmctrl_skips_malformed(self):
        for bad in ("", "   ", "garbage in a pipe",
                    "0xZZ 0 0 0 0 0 0 cls host title",
                    "1234 0 42 0 0 800 600 cls host title",
                    "0x05800006 notanint 50 0 800 600 cls host title",
                    "0x05800006 0 42 0 0 800 600"):
            with self.subTest(bad=bad):
                self.assertIsNone(linux_mod.parse_wmctrl(bad))

    def test_parse_xwininfo_tree_children(self):
        self.assertEqual(linux_mod.parse_xwininfo_tree(self.XWININFO_TREE), [
            {"hwnd": 0x380003e, "class": "Nemo-desktop", "title": "Masaüstü"},
            {"hwnd": 0x380003f, "class": "", "title": ""},
            {"hwnd": 0x360002a, "class": "Xmessage", "title": "xmessage"},
        ])

    # Real captured `xwininfo -root -tree` fragment: the header lines that
    # must be skipped, a terminal reparented under Cinnamon's frame wrapper,
    # a root-child desktop window, and a malformed line with no geometry.
    XWININFO_FULL_TREE = (
        'xwininfo: Window id: 0x637 (the root window) (has no name)\n'
        '\n'
        '  Root window id: 0x637 (the root window) (has no name)\n'
        '  Parent window id: 0x0 (none)\n'
        '     138 children:\n'
        '     0x260012b (has no name): ()  1924x1044+-2+38  +-2+38\n'
        '        1 child:\n'
        '        0x4c00006 "OC | Implement Task 10: live acceptance su…": '
        '("gnome-terminal-server" "Gnome-terminal")  1920x1008+2+34  +0+72\n'
        '           1 child:\n'
        '           0x4c00007 (has no name): ()  1x1+-1+-1  +-1+71\n'
        '     0x2e0003e "Masaüstü": ("nemo-desktop" "Nemo-desktop")  '
        '1920x1040+0+40  +0+40\n'
        '        1 child:\n'
        '        0x2e0003f (has no name): ()  1x1+-1+-1  +-1+39\n'
        '     0x00a00001 (has no name): ()\n'
    )

    def test_parse_xwininfo_full_tree(self):
        tree = linux_mod.parse_xwininfo_full_tree(self.XWININFO_FULL_TREE)
        self.assertEqual(tree[0x260012b]["rect"], [-2, 38, 1922, 1082])
        self.assertIsNone(tree[0x260012b]["parent"])
        self.assertEqual(tree[0x4c00006]["rect"], [0, 72, 1920, 1080])
        self.assertEqual(tree[0x4c00006]["parent"], 0x260012b)
        self.assertEqual(tree[0x4c00007]["parent"], 0x4c00006)
        self.assertEqual(tree[0x2e0003e]["rect"], [0, 40, 1920, 1080])
        self.assertIsNone(tree[0x2e0003e]["parent"])
        self.assertEqual(tree[0x2e0003f]["parent"], 0x2e0003e)
        for skipped in (0x637, 0x00a00001):
            self.assertNotIn(skipped, tree)


class TestLinuxCloseSafety(unittest.TestCase):
    """close_window refuses before the mutating X call (brief Step 1)."""

    def test_close_window_refuses_title_mismatch(self):
        # No display needed: verification failure and refusal both happen
        # before any X call, so this is safe on display-less CI.
        backend = LinuxBackend()
        result = backend.close_window(999, expect_title="Nope")
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_close_window_mismatch_never_sends_ic(self):
        listing = ("0x000003e7 0 42 10 20 800 600 xmessage.Xmessage "
                   "testhost Some Other Title\n")

        def router(cmd, timeout=5.0):
            if cmd[:2] == ["xdotool", "getactivewindow"]:
                return "1"
            if cmd[:2] == ["wmctrl", "-lGpx"]:
                return listing
            raise AssertionError(f"unexpected command: {cmd}")

        with mock.patch.dict(os.environ,
                             {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            backend = LinuxBackend()
            with mock.patch("backends.linux.run_x11",
                            side_effect=router) as run:
                result = backend.close_window(999, expect_title="Nope")
        self.assertFalse(result["ok"])
        self.assertIn("title mismatch", result["error"])
        self.assertIn("Some Other Title", result["error"])
        for call in run.call_args_list:
            self.assertNotIn("-ic", call.args[0])

    def test_close_window_happy_path_sends_ic_then_polls(self):
        state = {"closed": False}
        listing = ("0x000003e7 0 42 10 20 800 600 xmessage.Xmessage "
                   "testhost Expected Title\n")

        def router(cmd, timeout=5.0):
            if cmd[:2] == ["xdotool", "getactivewindow"]:
                return "1"
            if cmd[:2] == ["wmctrl", "-lGpx"]:
                return "" if state["closed"] else listing
            if cmd == ["wmctrl", "-ic", "0x3e7"]:
                state["closed"] = True
                return ""
            raise AssertionError(f"unexpected command: {cmd}")

        with mock.patch.dict(os.environ,
                             {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            backend = LinuxBackend()
            with mock.patch("backends.linux.run_x11",
                            side_effect=router) as run:
                result = backend.close_window(0x3E7, expect_title="Expected")
        self.assertEqual(result, {"ok": True, "closed": True,
                                  "title": "Expected Title", "note": ""})
        cmds = [c.args[0] for c in run.call_args_list]
        ic = [c for c in cmds if "-ic" in c]
        self.assertEqual(ic, [["wmctrl", "-ic", "0x3e7"]])
        self.assertLess(cmds.index(["wmctrl", "-lGpx"]),
                        cmds.index(["wmctrl", "-ic", "0x3e7"]))


class TestLinuxKillAndProcess(unittest.TestCase):
    """kill_process validates pids before any call; process_name reads /proc."""

    def test_kill_process_refuses_before_any_call(self):
        backend = LinuxBackend()
        for pid in (1, 0, -5, os.getpid()):
            with self.subTest(pid=pid):
                with mock.patch("backends.linux.os.kill") as kill:
                    result = backend.kill_process(pid)
                self.assertIsInstance(result, dict)
                self.assertFalse(result["ok"])
                self.assertIn("output", result)
                kill.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "signal.SIGKILL is Linux-only")
    def test_kill_process_sends_sigkill(self):
        with mock.patch("backends.linux.os.kill") as kill:
            result = LinuxBackend().kill_process(4321)
        self.assertEqual(result, {"ok": True, "output": ""})
        kill.assert_called_once_with(4321, signal.SIGKILL)

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "signal.SIGKILL is Linux-only")
    def test_kill_process_clean_error_on_missing_pid(self):
        with mock.patch("backends.linux.os.kill",
                        side_effect=ProcessLookupError(3, "No such process")):
            result = LinuxBackend().kill_process(4321)
        self.assertEqual(result, {"ok": False,
                                  "output": "no such process (pid 4321)"})

    @unittest.skipUnless(os.path.exists("/proc/self/comm"),
                         "requires procfs (Linux)")
    def test_process_name_reads_proc_comm(self):
        with open("/proc/self/comm", encoding="utf-8") as fh:
            expected = fh.read().strip()
        backend = LinuxBackend()
        self.assertEqual(backend.process_name(os.getpid()), expected)
        self.assertIsNone(backend.process_name(0x7FFFFF00))


class TestLinuxKeysym(unittest.TestCase):
    """keysym_for: API key name -> X11 keysym int (values checked vs Xlib)."""

    def test_keysym_table(self):
        ks = linux_mod.keysym_for
        self.assertEqual(ks("enter"), 0xFF0D)
        self.assertEqual(ks("esc"), 0xFF1B)
        self.assertEqual(ks("up"), 0xFF52)
        self.assertEqual(ks("left"), 0xFF51)
        self.assertEqual(ks("pagedown"), 0xFF56)
        self.assertEqual(ks("backspace"), 0xFF08)
        self.assertEqual(ks("space"), 0x20)
        self.assertEqual(ks("a"), 0x61)
        self.assertEqual(ks("1"), 0x31)
        self.assertEqual(ks("ctrl"), 0xFFE3)
        self.assertEqual(ks("shift"), 0xFFE1)
        self.assertEqual(ks("alt"), 0xFFE9)
        self.assertEqual(ks("win"), 0xFFEB)
        for n in range(1, 13):
            self.assertEqual(ks(f"f{n}"), 0xFFBE + n - 1)
        with self.assertRaises(ValueError):
            ks("totally-unknown-key")


class TestLinuxWindowContracts(unittest.TestCase):
    """Exact window invocation contracts with the runner mocked out."""

    def setUp(self):
        env = mock.patch.dict(os.environ,
                              {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
        env.start()
        self.addCleanup(env.stop)
        patcher = mock.patch("backends.linux.run_x11")
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.active = 0x1000
        self.listing = (
            "0x00001000 0 42 100 200 800 600 xmessage.Xmessage "
            "testhost Known Window\n"
        )
        self.geometry = ('xwininfo: Window id: 0x1000 "Known Window"\n'
                         '\n'
                         '  Absolute upper-left X:  50\n'
                         '  Absolute upper-left Y:  82\n'
                         '  Width: 800\n'
                         '  Height: 600\n')
        self.tree = (
            'xwininfo: Window id: 0x1000 "Known Window"\n'
            '\n'
            '  Root window id: 0x637 (the root window) (has no name)\n'
            '  Parent window id: 0x2c07d1a (has no name)\n'
            '     2 children:\n'
            '     0x100001 "frame": ("gtk" "GtkWindow")  100x100+0+0  +10+20\n'
            '        1 child:\n'
            '        0x100002 "box": ("inst" "TextField")  80x20+4+4  '
            '+14+24\n'
        )
        self.full_tree = (
            'xwininfo: Window id: 0x637 (the root window) (has no name)\n'
            '\n'
            '  Root window id: 0x637 (the root window) (has no name)\n'
            '  Parent window id: 0x0 (none)\n'
            '     3 children:\n'
            '     0x2000000 (has no name): ()  840x640+90+190  +90+190\n'
            '        1 child:\n'
            '        0x00001000 "Known Window": ("xmessage" "Xmessage")  '
            '800x600+10+10  +100+200\n'
            '     0x00003000 "Other": ("xmessage" "Xmessage")  '
            '100x100+0+0  +5+6\n'
            '        1 child:\n'
            '        0x300001 (has no name): ()  1x1+-1+-1  +-1+-1\n'
        )
        self.run.side_effect = self._router
        self.backend = LinuxBackend()

    def _router(self, cmd, timeout=5.0):
        if cmd[:2] == ["xdotool", "getactivewindow"]:
            return str(self.active)
        if cmd[:2] == ["wmctrl", "-lGpx"]:
            return self.listing
        if cmd[:2] == ["wmctrl", "-ic", "0x1000"]:
            return ""
        if cmd == ["xwininfo", "-root", "-tree"]:
            return self.full_tree
        if cmd[0] == "xwininfo":
            return self.tree if "-tree" in cmd else self.geometry
        return ""

    def calls(self):
        return [c.args[0] for c in self.run.call_args_list]

    def test_list_windows_filters_and_marks_focus(self):
        self.listing = (
            "0x00001000 0 42 100 200 800 600 xmessage.Xmessage "
            "testhost Known Window\n"
            "0x00002000 0 0 0 0 10 10 xmessage.Xmessage testhost \n"
            "not a window line at all\n"
            "0x00003000 0 4194305 5 6 7 8 xmessage.Xmessage testhost Other\n"
            "0x00004000 0 7 50 60 70 80 xmessage.Xmessage testhost Gone\n"
        )
        wins = self.backend.list_windows()
        self.assertEqual(len(wins), 2)
        known = next(w for w in wins if w["hwnd"] == 0x1000)
        self.assertEqual(known, {
            "hwnd": 0x1000, "title": "Known Window",
            "process": linux_mod.process_name(42) or "?",
            "pid": 42, "focused": True, "rect": [90, 190, 930, 830],
        })
        other = next(w for w in wins if w["hwnd"] == 0x3000)
        self.assertEqual(other["process"],
                         linux_mod.process_name(4194305) or "?")
        self.assertFalse(other["focused"])
        self.assertEqual(other["rect"], [5, 6, 105, 106])
        self.assertNotIn(0x4000, [w["hwnd"] for w in wins])
        self.assertEqual(self.calls()[0], ["wmctrl", "-lGpx"])
        self.assertEqual(self.calls()[1], ["xdotool", "getactivewindow"])
        self.assertEqual(self.calls()[2], ["xwininfo", "-root", "-tree"])

    def test_get_focused_window_entry_or_none(self):
        got = self.backend.get_focused_window()
        self.assertEqual(got["hwnd"], 0x1000)
        self.active = 0x9999
        self.assertIsNone(self.backend.get_focused_window())

    def test_focus_window_activate_then_verify(self):
        self.assertIsNone(self.backend.focus_window(0x1000))
        self.assertEqual(self.calls(), [
            ["xdotool", "windowactivate", "--sync", "0x1000"],
            ["xdotool", "getactivewindow"],
        ])
        self.active = 0x9999
        with self.assertRaises(RuntimeError):
            self.backend.focus_window(0x1000)

    def test_focus_window_activation_error_propagates(self):
        self.run.side_effect = RuntimeError("BadWindow")
        with self.assertRaises(RuntimeError):
            self.backend.focus_window(0x1000)

    def test_probe_input_mode_never_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.backend.probe_input_mode(0x1000), "invalid")
        self.assertEqual(self.backend.probe_input_mode(0x1000), "focused")
        self.assertEqual(self.backend.probe_input_mode(0xDEAD), "invalid")
        self.run.side_effect = ApiError("BACKEND_UNAVAILABLE", "gone",
                                        status=501)
        self.assertEqual(self.backend.probe_input_mode(0x1000), "invalid")

    def test_maximize_and_set_topmost_invocations(self):
        self.backend.maximize_window(0x1000)
        self.backend.set_topmost(0x1000, True)
        self.backend.set_topmost(0x1000, False)
        self.assertEqual(self.calls(), [
            ["wmctrl", "-ir", "0x1000", "-b",
             "add,maximized_vert,maximized_horz"],
            ["wmctrl", "-ir", "0x1000", "-b", "add,above"],
            ["wmctrl", "-ir", "0x1000", "-b", "remove,above"],
        ])

    def test_list_children_and_pick_input_child(self):
        children = self.backend.list_children(0x1000)
        self.assertEqual(children, [
            {"hwnd": 0x100001, "class": "GtkWindow", "title": "frame"},
            {"hwnd": 0x100002, "class": "TextField", "title": "box"},
        ])
        self.assertEqual(self.calls()[0],
                         ["xwininfo", "-id", "0x1000", "-tree"])
        self.assertEqual(self.backend.pick_input_child(0x1000), 0x100002)
        self.run.side_effect = RuntimeError("window gone")
        self.assertEqual(self.backend.list_children(0x999), [])

    def test_client_to_screen_adds_origin(self):
        self.assertEqual(self.backend.client_to_screen(0x1000, 10, 20),
                         (60, 102))
        self.assertEqual(self.calls()[0], ["xwininfo", "-id", "0x1000"])

    def test_client_to_screen_value_error_when_gone(self):
        self.run.side_effect = RuntimeError("BadWindow")
        with self.assertRaises(ValueError):
            self.backend.client_to_screen(0x1000, 0, 0)

    def test_window_key_focus_first_and_vk(self):
        result = self.backend.window_key(0x1000, "enter")
        self.assertEqual(result, {"ok": True, "vk": 0xFF0D})
        cmds = self.calls()
        self.assertEqual(cmds[0], ["xdotool", "windowactivate", "--sync",
                                   "0x1000"])
        self.assertIn(["xdotool", "key", "Return"], cmds)

    def test_window_key_and_hotkey_forbidden_before_any_call(self):
        with self.assertRaises(PermissionError):
            self.backend.window_key(0x1000, "delete")
        self.assertEqual(self.calls(), [])
        with self.assertRaises(PermissionError):
            self.backend.window_hotkey(0x1000, ["alt", "f4"])
        self.assertEqual(self.calls(), [])

    def test_window_key_and_hotkey_refuse_alias_vectors_before_any_call(self):
        with self.assertRaises(ValueError) as ctx:
            self.backend.window_key(0x1000, "alt+f4")
        self.assertIn("chord", str(ctx.exception))
        self.assertEqual(self.calls(), [])
        with self.assertRaises(PermissionError):
            self.backend.window_key(0x1000, "super")
        self.assertEqual(self.calls(), [])
        with self.assertRaises(PermissionError):
            self.backend.window_hotkey(0x1000, ["super", "l"])
        self.assertEqual(self.calls(), [])
        with self.assertRaises(PermissionError):
            self.backend.window_hotkey(0x1000, ["alt+f4"])
        self.assertEqual(self.calls(), [])

    def test_window_hotkey_returns_chord_keysyms(self):
        result = self.backend.window_hotkey(0x1000, ["ctrl", "s"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["vk"], 0xFFE3)
        self.assertEqual(result["vks"], [0xFFE3, 0x73])
        self.assertIn(["xdotool", "key", "ctrl+s"], self.calls())

    def test_window_type_text_focus_first_and_chars(self):
        result = self.backend.window_type_text(0x1000, "hi\nthere")
        self.assertEqual(result, {"ok": True, "chars": 8})
        cmds = self.calls()
        self.assertEqual(cmds[0], ["xdotool", "windowactivate", "--sync",
                                   "0x1000"])
        self.assertIn(["xdotool", "type", "--delay", "30", "--", "hi"], cmds)
        self.assertIn(["xdotool", "type", "--delay", "30", "--", "there"],
                      cmds)

    def test_window_click_converts_client_to_screen(self):
        result = self.backend.window_click(0x1000, 10, 20, button="right",
                                           clicks=2)
        self.assertEqual(result, {"ok": True, "x": 10, "y": 20,
                                  "button": "right", "clicks": 2})
        cmds = self.calls()
        self.assertEqual(cmds[-2], ["xdotool", "mousemove", "--sync",
                                    "60", "102"])
        self.assertEqual(cmds[-1], ["xdotool", "click", "--repeat", "2", "3"])

    def test_window_scroll_parks_pointer_over_window(self):
        result = self.backend.window_scroll(0x1000, -2)
        self.assertEqual(result, {"ok": True, "clicks": -2})
        cmds = self.calls()
        self.assertEqual(cmds[-2], ["xdotool", "mousemove", "--sync",
                                    "450", "382"])
        self.assertEqual(cmds[-1], ["xdotool", "click", "--repeat", "2", "5"])

    def test_window_drag_returns_from_to(self):
        result = self.backend.window_drag(0x1000, 1, 2, 3, 4)
        self.assertEqual(result, {"ok": True, "from": [1, 2], "to": [3, 4]})
        cmds = self.calls()
        self.assertEqual(cmds[0], ["xdotool", "windowactivate", "--sync",
                                   "0x1000"])
        self.assertIn(["xdotool", "mousedown", "1"], cmds)
        self.assertIn(["xdotool", "mousemove", "--sync", "53", "86"], cmds)
        self.assertEqual(cmds[-1], ["xdotool", "mouseup", "1"])


@unittest.skipUnless(os.environ.get("DISPLAY"), "requires an X11 DISPLAY")
class TestLinuxLiveWindows(unittest.TestCase):
    """Brief Step 1 live window tests — windows this test opens itself.

    A shared desktop may host interactive clients, so the tests only ever
    touch their own xmessage windows, restore the previously active window
    on the way out, and skip when another X client keeps stealing focus.
    """

    def setUp(self):
        self.backend = LinuxBackend()
        self.procs = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        self.procs.clear()

    def _spawn(self, title):
        proc = subprocess.Popen(
            ["xmessage", "-buttons", "", "-title", title, "t7"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(proc)
        return proc

    def _find(self, title, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for win in self.backend.list_windows():
                if win["title"] == title:
                    return win
            time.sleep(0.1)
        return None

    def _read_active(self):
        try:
            raw = run_x11(["xdotool", "getactivewindow"]).strip()
        except (ApiError, RuntimeError):
            return None
        try:
            return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
        except ValueError:
            return None

    def test_list_windows_contains_known_window(self):
        self._spawn("SC-T7-list")
        found = self._find("SC-T7-list")
        if found is None:
            self.skipTest("xmessage window did not appear in the window list")
        self.assertEqual(found["title"], "SC-T7-list")
        self.assertIsInstance(found["hwnd"], int)
        self.assertEqual(len(found["rect"]), 4)
        self.assertIsInstance(found["pid"], int)
        self.assertIsInstance(found["focused"], bool)
        self.assertEqual(found["process"],
                         linux_mod.process_name(found["pid"]) or "?")
        # focused flag must agree with the live active window (retry once
        # if focus changed between the two reads).
        for _ in range(3):
            wins = self.backend.list_windows()
            mine = next((w for w in wins if w["title"] == "SC-T7-list"), None)
            active = self._read_active()
            if mine is not None and mine["focused"] == (mine["hwnd"] == active):
                break
            time.sleep(0.2)
        else:
            self.skipTest("focus kept changing while checking focused flag")
        self.assertEqual(mine["focused"], mine["hwnd"] == active)

    def test_focus_window_makes_it_active(self):
        self._spawn("SC-T7-focus-a")
        self._spawn("SC-T7-focus-b")
        restore = self._read_active()
        wa = self._find("SC-T7-focus-a")
        wb = self._find("SC-T7-focus-b")
        if wa is None or wb is None:
            self.skipTest("xmessage windows did not appear")
        try:
            for hwnd in (wa["hwnd"], wb["hwnd"]):
                for _ in range(3):
                    try:
                        self.backend.focus_window(hwnd)
                        break
                    except RuntimeError:
                        time.sleep(0.3)  # stolen; retry
                else:
                    self.skipTest("focus stolen by another X client; "
                                  "refusing to keep fighting over focus")
                if self._read_active() != hwnd:
                    self.skipTest("active window changed right after focus")
        finally:
            if restore not in (None, wa["hwnd"], wb["hwnd"]):
                try:
                    run_x11(["xdotool", "windowactivate", hex(restore)])
                except (ApiError, RuntimeError):
                    pass

    def test_focus_unknown_hwnd_raises_runtime(self):
        with self.assertRaises(RuntimeError):
            self.backend.focus_window(0x7FFFFFFE)

    def test_client_to_screen_matches_geometry(self):
        from Xlib import display as xdisplay
        self._spawn("SC-T7-geo")
        found = self._find("SC-T7-geo")
        if found is None:
            self.skipTest("xmessage window did not appear")
        disp = xdisplay.Display()
        self.addCleanup(disp.close)
        xwin = disp.create_resource_object("window", found["hwnd"])

        def origin():
            rel = disp.screen().root.translate_coords(xwin, 0, 0)
            return (rel.x, rel.y)

        before = origin()
        got = self.backend.client_to_screen(found["hwnd"], 0, 0)
        after = origin()
        if before != after:
            self.skipTest("window moved while its origin was measured")
        self.assertEqual(got, before)
        self.assertEqual(self.backend.client_to_screen(found["hwnd"], 10, 20),
                         (before[0] + 10, before[1] + 20))

    def test_list_windows_rect_contains_own_client_origin(self):
        self._spawn("SC-rect-inside")
        found = self._find("SC-rect-inside")
        if found is None:
            self.skipTest("xmessage window did not appear in the window list")
        left, top, right, bottom = found["rect"]
        cx, cy = self.backend.client_to_screen(found["hwnd"], 0, 0)
        self.assertLessEqual(left, cx)
        self.assertLess(cx, right)
        self.assertLessEqual(top, cy)
        self.assertLess(cy, bottom)
        width, height = self.backend.screen_size()
        self.assertLessEqual(right, width + 100)
        self.assertLessEqual(bottom, height + 100)
        self.assertGreater(right - left, 10)
        self.assertGreater(bottom - top, 10)


class TestLinuxCaptureContracts(unittest.TestCase):
    """Task 8 capture failure mapping — RuntimeError means server 409."""

    def setUp(self):
        env = mock.patch.dict(os.environ,
                              {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
        env.start()
        self.addCleanup(env.stop)

    def test_import_failure_raises_runtime_error(self):
        failed = subprocess.CompletedProcess(
            ["import"], returncode=1,
            stderr=b"import: no window with specified ID exists\n")
        with mock.patch("backends.linux.shutil.which",
                        return_value="/usr/bin/import"):
            with mock.patch("backends.linux.subprocess.run",
                            return_value=failed):
                with self.assertRaises(RuntimeError) as ctx:
                    LinuxBackend().capture_window(0x7FFFFFFE)
        self.assertIn("import failed", str(ctx.exception))
        self.assertIn("0x7ffffffe", str(ctx.exception))

    def test_fallback_grab_failure_raises_runtime_error(self):
        geometry = ('xwininfo: Window id: 0x1000 "Offscreen"\n'
                    '\n'
                    '  Absolute upper-left X:  -120\n'
                    '  Absolute upper-left Y:  82\n'
                    '  Width: 800\n'
                    '  Height: 600\n')
        instance = mock.MagicMock()
        grab = instance.__enter__.return_value.grab
        grab.side_effect = linux_mod.mss.ScreenShotError(
            "X error while grabbing the region")
        with mock.patch("backends.linux.shutil.which", return_value=None):
            with mock.patch("backends.linux.run_x11",
                            return_value=geometry):
                with mock.patch("backends.linux.mss.MSS",
                                return_value=instance):
                    with self.assertRaises(RuntimeError) as ctx:
                        LinuxBackend().capture_window(0x1000)
        self.assertNotIsInstance(ctx.exception,
                                 linux_mod.mss.ScreenShotError)
        self.assertIsInstance(ctx.exception.__cause__,
                              linux_mod.mss.ScreenShotError)
        self.assertIn("screen grab failed", str(ctx.exception))
        self.assertIn("0x1000", str(ctx.exception))
        grab.assert_called_once_with({"left": -120, "top": 82,
                                      "width": 800, "height": 600})


@unittest.skipUnless(os.environ.get("DISPLAY"), "requires an X11 DISPLAY")
class TestLinuxLiveCapture(unittest.TestCase):
    """Task 8 live capture tests — self-contained on a shared desktop.

    Screen grabs are read-only, but capture_window must never be pointed
    at foreign windows: the window tests open and focus their own
    xmessage windows and skip when another X client keeps the focus.
    """

    def setUp(self):
        self.backend = LinuxBackend()
        self.procs = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        self.procs.clear()

    def _spawn(self, title):
        proc = subprocess.Popen(
            ["xmessage", "-buttons", "", "-title", title, "t8"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(proc)
        return proc

    def _find(self, title, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for win in self.backend.list_windows():
                if win["title"] == title:
                    return win
            time.sleep(0.1)
        return None

    def _read_active(self):
        try:
            raw = run_x11(["xdotool", "getactivewindow"]).strip()
        except (ApiError, RuntimeError):
            return None
        try:
            return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
        except ValueError:
            return None

    def test_screen_size_matches_xrandr(self):
        width, height = self.backend.screen_size()
        out = run_x11(["xdotool", "getdisplaygeometry"]).split()
        self.assertEqual((width, height), (int(out[0]), int(out[1])))

    def test_screenshot_region_size(self):
        img = self.backend.screenshot(region=(0, 0, 50, 40))
        self.assertEqual(img.size, (50, 40))
        self.assertEqual(img.mode, "RGB")

    def test_screenshot_jpeg_magic_bytes(self):
        data = self.backend.screenshot_jpeg()
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b"\xff\xd8"))

    def test_screenshot_scaled_half(self):
        full = self.backend.screenshot()
        half = self.backend.screenshot_scaled(scale=0.5)
        self.assertEqual(half.size, (full.width // 2, full.height // 2))

    def test_list_monitors_shape(self):
        monitors = self.backend.list_monitors()
        self.assertGreaterEqual(len(monitors), 1)
        first = monitors[0]
        self.assertEqual(set(first), {"id", "name", "left", "top", "width",
                                      "height", "is_primary"})
        self.assertEqual(first["id"], 1)
        self.assertIs(first["is_primary"], True)
        self.assertGreater(first["width"], 0)
        self.assertGreater(first["height"], 0)

    def test_frame_diff_between_two_captures(self):
        prev = self.backend.screenshot(region=(0, 0, 64, 48))
        cur = self.backend.screenshot(region=(0, 0, 64, 48))
        diff = self.backend.frame_diff(prev, cur)
        self.assertEqual(set(diff), {"changed", "bbox", "changed_pct",
                                     "tiles"})
        self.assertIsInstance(diff["changed"], bool)
        self.assertIsInstance(diff["changed_pct"], float)
        self.assertIsInstance(diff["tiles"], list)

    def test_capture_window_of_active_window(self):
        self._spawn("SC-T8-capture")
        found = self._find("SC-T8-capture")
        if found is None:
            self.skipTest("xmessage window did not appear in the window list")
        for _ in range(3):
            try:
                self.backend.focus_window(found["hwnd"])
                break
            except (ApiError, RuntimeError):
                time.sleep(0.3)
        else:
            self.skipTest("focus stolen by another X client before capture")
        if self._read_active() != found["hwnd"]:
            self.skipTest("active window changed to a foreign window")
        img = self.backend.capture_window(found["hwnd"])
        self.assertGreater(img.width, 10)
        self.assertGreater(img.height, 10)
        self.assertEqual(img.mode, "RGB")
        client = self.backend.capture_window(found["hwnd"],
                                             client_only=True)
        self.assertEqual(client.size, img.size)

    def test_capture_window_mss_fallback_grabs_geometry(self):
        geometry = ('xwininfo: Window id: 0x1000 "Fallback"\n'
                    '\n'
                    '  Absolute upper-left X:  50\n'
                    '  Absolute upper-left Y:  82\n'
                    '  Width: 800\n'
                    '  Height: 600\n')
        with mock.patch("backends.linux.shutil.which", return_value=None):
            with mock.patch("backends.linux.run_x11",
                            return_value=geometry):
                img = self.backend.capture_window(0x1000)
        self.assertEqual(img.size, (800, 600))
        self.assertEqual(img.mode, "RGB")


@unittest.skipUnless(os.environ.get("DISPLAY")
                     and os.access("/dev/uinput", os.R_OK | os.W_OK),
                     "requires an X11 DISPLAY and a writable /dev/uinput")
class TestLinuxLiveGame(unittest.TestCase):
    """Task 9 live uinput test — only ever touches its own xev window.

    The desktop is shared with a human session and possibly a fullscreen
    game, so the test spawns xev under a pid-unique name, focuses it,
    skips whenever another X client holds the focus, and stops game mode
    on the way out (release_all must keep working after game_stop).
    """

    def setUp(self):
        self.backend = LinuxBackend()
        self.procs = []
        self.addCleanup(self._cleanup)
        self.addCleanup(self.backend.release_all)
        self.addCleanup(self.backend.game_stop)

    def _cleanup(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        self.procs.clear()

    def test_game_mode_uinput_events_reach_x11(self):
        name = f"SC-T9-gameev-{os.getpid()}"
        proc = subprocess.Popen(["xev", "-name", name,
                                 "-geometry", "200x200+400+300"],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        self.procs.append(proc)
        try:
            wid = run_x11(["xdotool", "search", "--sync", "--name",
                           name]).split()[-1]
            run_x11(["xdotool", "windowfocus", "--sync", wid])
            time.sleep(0.2)
            if run_x11(["xdotool", "getactivewindow"]).strip() != wid:
                self.skipTest("focus stolen by another X client; "
                              "refusing to send game events to it")
            win = next((w for w in self.backend.list_windows()
                        if str(w["hwnd"]) == wid), None)
            if win is None:
                self.skipTest("own xev window disappeared from the list")
            left, top, right, bottom = win["rect"]
            try:
                mouse_move((left + right) // 2, (top + bottom) // 2)
            except ApiError as exc:
                if exc.code != "OPERATION_TIMEOUT":
                    raise
                self.skipTest("pointer warp blocked by another X client")
            time.sleep(0.3)
            started = self.backend.game_start(sensitivity=1)
            self.assertTrue(started["ok"])
            pos = dict(line.split("=", 1) for line in
                       run_x11(["xdotool", "getmouselocation",
                                "--shell"]).splitlines() if "=" in line)
            before_x, before_y = int(pos["X"]), int(pos["Y"])
            self.backend.game_move(5, 0, sensitivity=1)
            time.sleep(0.3)
            if run_x11(["xdotool", "getactivewindow"]).strip() != wid:
                self.skipTest("focus stolen before key events; "
                              "refusing to send keys to a foreign window")
            self.backend.key_down("w")
            time.sleep(0.3)
            self.backend.game_stop()
            self.assertFalse(self.backend.game_active())
            self.assertTrue(self.backend.release_all()["ok"])
        finally:
            proc.terminate()
            out, _ = proc.communicate(timeout=10)
        motions = re.findall(
            r"MotionNotify event[^\n]*\n[^\n]*root:\((\d+),(\d+)\)", out)
        self.assertTrue(motions, "xev saw no MotionNotify events")
        self.assertGreaterEqual(
            max(max(abs(int(x) - before_x), abs(int(y) - before_y))
                for x, y in motions), 1)
        self.assertIn("KeyPress event", out)
        self.assertIn("keysym 0x77, w", out)


if __name__ == "__main__":
    unittest.main()
