"""LinuxBackend skeleton: display detection, Wayland fail-fast, capabilities.

CI-safe: every test patches os.environ so no real display is required.
"""
import os
import unittest
from unittest import mock

from backends.linux import LinuxBackend
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


if __name__ == "__main__":
    unittest.main()
