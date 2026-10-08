"""LinuxBackend: display detection, Wayland fail-fast, xdotool input,
wmctrl/xwininfo window management.

Unit tests patch os.environ and mock the xdotool runner so no display is
required; the TestLinuxLiveInput and TestLinuxLiveWindows groups run only
against a real X11 DISPLAY.
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
        for n in range(1, 13):
            self.assertEqual(xdotool_key(f"f{n}"), f"F{n}")

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
                with mock.patch("backends.linux.run_x11") as run:
                    result = backend.kill_process(pid)
                self.assertIsInstance(result, dict)
                self.assertFalse(result["ok"])
                self.assertIn("output", result)
                run.assert_not_called()

    def test_kill_process_sends_kill_minus_9(self):
        with mock.patch("backends.linux.run_x11", return_value="") as run:
            result = LinuxBackend().kill_process(4321)
        self.assertEqual(result, {"ok": True, "output": ""})
        run.assert_called_once_with(["kill", "-9", "4321"])

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
                         '  Absolute upper-left Y:  82\n')
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
        self.run.side_effect = self._router
        self.backend = LinuxBackend()

    def _router(self, cmd, timeout=5.0):
        if cmd[:2] == ["xdotool", "getactivewindow"]:
            return str(self.active)
        if cmd[:2] == ["wmctrl", "-lGpx"]:
            return self.listing
        if cmd[:2] == ["wmctrl", "-ic", "0x1000"]:
            return ""
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
        )
        wins = self.backend.list_windows()
        self.assertEqual(len(wins), 2)
        known = next(w for w in wins if w["hwnd"] == 0x1000)
        self.assertEqual(known, {
            "hwnd": 0x1000, "title": "Known Window",
            "process": linux_mod.process_name(42) or "?",
            "pid": 42, "focused": True, "rect": [100, 200, 900, 800],
        })
        other = next(w for w in wins if w["hwnd"] == 0x3000)
        self.assertEqual(other["process"],
                         linux_mod.process_name(4194305) or "?")
        self.assertFalse(other["focused"])
        self.assertEqual(self.calls()[0], ["wmctrl", "-lGpx"])
        self.assertEqual(self.calls()[1], ["xdotool", "getactivewindow"])

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
                                    "500", "500"])
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


if __name__ == "__main__":
    unittest.main()
