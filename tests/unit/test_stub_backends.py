"""Stub backends must fail closed with BACKEND_UNAVAILABLE, never pretend."""
import unittest

from backends.linux import LinuxBackend
from backends.macos import MacOSBackend
from core.errors import ApiError


class TestStubBackends(unittest.TestCase):
    def test_every_action_raises_backend_unavailable(self):
        for backend, platform_name in ((LinuxBackend(), "linux"),
                                       (MacOSBackend(), "macos")):
            with self.subTest(platform=platform_name):
                caps = backend.get_capabilities()
                self.assertEqual(caps["platform"], platform_name)
                self.assertIsNone(caps["screen_capture"])
                actions = [
                    ("screen_size", lambda b: b.screen_size()),
                    ("screenshot", lambda b: b.screenshot()),
                    ("screenshot_jpeg", lambda b: b.screenshot_jpeg()),
                    ("mouse_move", lambda b: b.mouse_move(1, 2)),
                    ("mouse_click", lambda b: b.mouse_click(1, 2)),
                    ("key_press", lambda b: b.key_press("a")),
                    ("type_text", lambda b: b.type_text("hi")),
                    ("list_windows", lambda b: b.list_windows()),
                    ("get_focused_window", lambda b: b.get_focused_window()),
                    ("focus_window", lambda b: b.focus_window(1)),
                    ("close_window", lambda b: b.close_window(1)),
                    ("kill_process", lambda b: b.kill_process(1)),
                    ("capture_window", lambda b: b.capture_window(1)),
                    ("maximize_window", lambda b: b.maximize_window(1)),
                    ("set_topmost", lambda b: b.set_topmost(1, True)),
                    ("window_click", lambda b: b.window_click(1, 0, 0)),
                    ("list_children", lambda b: b.list_children(1)),
                    ("game_start", lambda b: b.game_start()),
                    ("client_to_screen", lambda b: b.client_to_screen(1, 0, 0)),
                    ("process_name", lambda b: b.process_name(1)),
                ]
                for name, call in actions:
                    with self.subTest(action=name):
                        with self.assertRaises(ApiError) as ctx:
                            call(backend)
                        self.assertEqual(ctx.exception.code,
                                         "BACKEND_UNAVAILABLE")
                        self.assertEqual(ctx.exception.status, 501)

    def test_read_only_methods_fail_safe(self):
        for backend in (LinuxBackend(), MacOSBackend()):
            with self.subTest(backend=type(backend).__name__):
                self.assertEqual(backend.held_state(),
                                 {"keys": [], "buttons": []})
                self.assertEqual(backend.release_all(),
                                 {"ok": True, "released": []})
                self.assertFalse(backend.game_active())
                self.assertEqual(backend.probe_input_mode(1), "invalid")

    def test_stub_keeps_forbidden_key_policy(self):
        for backend in (LinuxBackend(), MacOSBackend()):
            with self.subTest(backend=type(backend).__name__):
                self.assertIsNone(backend.assert_allowed(["a"]))
                with self.assertRaises(PermissionError):
                    backend.assert_allowed(["alt", "f4"])


if __name__ == "__main__":
    unittest.main()
