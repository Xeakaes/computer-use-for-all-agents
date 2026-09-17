"""Unit tests for the PlatformBackend abstract interface."""
import unittest

from core.backends import PlatformBackend, create_backend

EXPECTED_METHODS = {
    "get_capabilities", "screen_size", "list_monitors",
    "screenshot", "screenshot_jpeg", "screenshot_scaled", "frame_diff",
    "capture_window",
    "mouse_move", "mouse_click", "mouse_scroll", "mouse_drag",
    "mouse_move_relative", "mouse_down", "mouse_up",
    "assert_allowed", "key_press", "key_down", "key_up", "key_hotkey",
    "type_text", "held_state", "release_all",
    "list_windows", "get_focused_window", "focus_window",
    "close_window", "kill_process", "process_name", "probe_input_mode",
    "window_type_text", "window_key", "window_hotkey",
    "window_click", "window_scroll", "window_drag",
    "list_children", "pick_input_child", "client_to_screen",
    "maximize_window", "set_topmost",
    "game_start", "game_move", "game_stop", "game_active",
}


class TestPlatformBackend(unittest.TestCase):
    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            PlatformBackend()  # type: ignore[abstract]

    def test_abstract_surface_is_complete(self):
        actual = {n for n, v in vars(PlatformBackend).items()
                  if callable(v) and not n.startswith("_")}
        missing = EXPECTED_METHODS - actual
        self.assertFalse(missing, f"missing interface methods: {sorted(missing)}")

    def test_create_backend_rejects_unknown_name(self):
        with self.assertRaises(ValueError):
            create_backend("amiga")


if __name__ == "__main__":
    unittest.main()
