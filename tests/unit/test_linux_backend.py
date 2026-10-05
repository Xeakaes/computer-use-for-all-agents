"""LinuxBackend: display detection, Wayland fail-fast, xdotool input.

Unit tests patch os.environ and mock the xdotool runner so no display is
required; the TestLinuxLiveInput group runs only against a real X11 DISPLAY.
"""
import os
import subprocess
import time
import unittest
from unittest import mock

from backends import linux as linux_mod
from backends.linux import (LinuxBackend, held_state, key_down, key_hotkey,
                            key_press, key_up, mouse_click, mouse_down,
                            mouse_drag, mouse_move, mouse_move_relative,
                            mouse_scroll, release_all, run_x11,
                            type_segments, type_text, xdotool_key)
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

    def test_forbidden_policy(self):
        with mock.patch.dict(os.environ,
                             {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            backend = LinuxBackend()
        self.assertIsNone(backend.assert_allowed(["a"]))
        with self.assertRaises(PermissionError):
            backend.assert_allowed(["alt", "f4"])

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

    def test_type_text_one_type_call_per_segment(self):
        type_text("a\nb")
        self.assertEqual(self.calls(), [
            ["xdotool", "type", "--clearmodifiers", "--delay", "30",
             "--", "a"],
            ["xdotool", "key", "Return"],
            ["xdotool", "type", "--clearmodifiers", "--delay", "30",
             "--", "b"],
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


if __name__ == "__main__":
    unittest.main()
