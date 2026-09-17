"""Server integration over the FakeBackend — no real screen, input, or windows."""
import unittest

from tests.unit.fakes import make_fake_server


class TestServerRoutesWithFakeBackend(unittest.TestCase):
    def setUp(self):
        self.client, self.backend = make_fake_server()

    def test_info_reports_backend_and_platform(self):
        r = self.client.get("/api/info")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["backend"], "fake")

    def test_capabilities_endpoint(self):
        r = self.client.get("/api/capabilities")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["backend"], "fake")
        self.assertEqual(body["capabilities"]["platform"], "fake")

    def test_capabilities_requires_auth(self):
        r = self.client._client.get("/api/capabilities")
        self.assertEqual(r.status_code, 401)

    def test_mouse_click_routes_to_backend(self):
        r = self.client.post("/api/mouse", json={"action": "click",
                                                 "x": 10, "y": 20})
        self.assertEqual(r.status_code, 200)
        self.assertIn(("mouse_click", (10, 20), {"button": "left", "clicks": 1}),
                      self.backend.calls)

    def test_key_forbidden_combo_403(self):
        r = self.client.post("/api/key", json={"action": "hotkey",
                                               "keys": ["alt", "f4"]})
        self.assertEqual(r.status_code, 403)
        body = r.get_json()
        self.assertEqual(body["error"]["code"], "INPUT_BLOCKED")
        self.assertNotIn("key_hotkey", [m for m, _, _ in self.backend.calls])

    def test_key_unknown_action_uses_envelope(self):
        r = self.client.post("/api/key", json={"action": "explode"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "INVALID_TARGET")

    def test_window_close_focus_guard_409(self):
        # Fake desktop: hwnd 101 is focused; closing 303 with expect_focus
        # must be refused with the standardized envelope.
        r = self.client.post("/api/window", json={"action": "close",
                                                  "hwnd": 303,
                                                  "expect_focus": True})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "FOCUS_MISMATCH")

    def test_window_close_happy_path(self):
        r = self.client.post("/api/window", json={"action": "close", "hwnd": 202})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertNotIn(202, [w["hwnd"] for w in self.backend.list_windows()])

    def test_window_close_unknown_hwnd_404(self):
        r = self.client.post("/api/window", json={"action": "close",
                                                  "hwnd": 99999})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"]["code"], "WINDOW_NOT_FOUND")

    def test_window_post_forbidden_key_403(self):
        r = self.client.post("/api/window/post", json={"hwnd": 101,
                                                       "action": "key",
                                                       "key": "win"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"]["code"], "INPUT_BLOCKED")

    def test_release_all_clears_fake_holds(self):
        self.backend.key_down("shift")
        r = self.client.post("/api/release_all", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.backend.held_state(), {"keys": [], "buttons": []})

    def test_auth_rejects_wrong_token(self):
        r = self.client._client.get("/api/info",
                                    headers={"X-Auth-Token": "wrong"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
