"""FakeBackend: records actions, never touches the OS."""
import unittest

from backends.fake import FakeBackend


class TestFakeBackend(unittest.TestCase):
    def setUp(self):
        self.b = FakeBackend()

    def test_fake_desktop_shape(self):
        wins = self.b.list_windows()
        self.assertEqual([w["hwnd"] for w in wins], [101, 202, 303])
        self.assertTrue(all(w["focused"] == (w["hwnd"] == 101) for w in wins))

    def test_input_is_recorded_not_sent(self):
        self.b.mouse_click(5, 6)
        self.b.key_press("a")
        self.b.type_text("hi")
        methods = [m for m, _, _ in self.b.calls]
        self.assertIn("mouse_click", methods)
        self.assertIn("key_press", methods)
        self.assertIn("type_text", methods)
        self.assertEqual(self.b.calls[-1],
                         ("type_text", ("hi",), {"interval": 0.03}))

    def test_held_state_and_release_all(self):
        self.b.key_down("shift")
        self.b.mouse_down("left")
        self.assertEqual(self.b.held_state(),
                         {"keys": ["shift"], "buttons": ["left"]})
        res = self.b.release_all()
        self.assertEqual(res, {"ok": True, "released": ["shift", "mouse:left"]})
        self.assertEqual(self.b.held_state(), {"keys": [], "buttons": []})

    def test_forbidden_hotkey_blocked(self):
        with self.assertRaises(PermissionError):
            self.b.key_hotkey("alt", "f4")
        self.assertNotIn("key_hotkey", [m for m, _, _ in self.b.calls])

    def test_game_mode_lifecycle(self):
        self.assertFalse(self.b.game_active())
        self.b.game_start()
        self.assertTrue(self.b.game_active())
        self.b.game_move(3, -3)
        self.b.game_stop()
        self.assertFalse(self.b.game_active())

    def test_close_window_matches_expectation(self):
        res = self.b.close_window(202, expect_title="Settings")
        self.assertTrue(res["ok"])
        res = self.b.close_window(202, expect_title="WRONG")
        self.assertFalse(res["ok"])

    def test_unknown_window_raises_window_not_found(self):
        from core.errors import ApiError
        with self.assertRaises(ApiError) as ctx:
            self.b.focus_window(99999)
        self.assertEqual(ctx.exception.code, "WINDOW_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
