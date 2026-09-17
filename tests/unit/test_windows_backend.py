"""WindowsBackend implements the full interface; control.py stays a working
compatibility layer with the same public names. Skipped on non-Windows
because the module imports pyautogui/Win32 at import time."""
import sys
import unittest


@unittest.skipUnless(sys.platform == "win32",
                     "imports Win32-only modules (pyautogui, ctypes windll)")
class TestWindowsBackendShape(unittest.TestCase):
    def test_is_platform_backend(self):
        from backends.windows import WindowsBackend
        from core.backends import PlatformBackend
        self.assertIsInstance(WindowsBackend(), PlatformBackend)

    def test_every_public_control_name_survives(self):
        import control
        for expected in ("screenshot", "screenshot_jpeg", "screenshot_scaled",
                         "frame_diff", "mouse_move", "mouse_click",
                         "mouse_scroll", "mouse_drag", "mouse_move_relative",
                         "mouse_down", "mouse_up", "key_press", "key_down",
                         "key_up", "key_hotkey", "type_text", "release_all",
                         "held_state", "list_windows", "get_focused_window",
                         "find_window", "focus_window", "close_window_safely",
                         "kill_process", "capture_window", "list_children",
                         "pick_input_child", "probe_input_mode",
                         "window_type_text", "window_key", "window_hotkey",
                         "window_click", "window_scroll", "window_drag",
                         "client_to_screen", "game_start", "game_move",
                         "game_stop", "screen_size", "list_monitors",
                         "get_platform", "process_name", "vk_from_name",
                         "IS_WINDOWS", "FORBIDDEN_KEYS", "FORBIDDEN_HOTKEYS"):
            self.assertTrue(hasattr(control, expected),
                            f"control.{expected} missing after refactor")

    def test_backend_methods_delegate_and_enforce_policy(self):
        from backends.windows import WindowsBackend
        b = WindowsBackend()
        self.assertEqual(b.game_active(), False)          # reads OS state, harmless
        with self.assertRaises(PermissionError):
            b.assert_allowed(["alt", "f4"])

    def test_forbidden_policy_unchanged(self):
        import control
        self.assertIn("win", control.FORBIDDEN_KEYS)
        self.assertIn(frozenset(("alt", "f4")), control.FORBIDDEN_HOTKEYS)


if __name__ == "__main__":
    unittest.main()
