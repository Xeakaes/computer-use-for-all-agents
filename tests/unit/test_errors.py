"""Unit tests for the standardized error envelope (ROADMAP Phase 2)."""
import unittest

from core.errors import (
    ERROR_CODES, ApiError, capture_failed, focus_mismatch,
    invalid_target, permission_required, unsupported_platform, window_not_found,
)


class TestApiError(unittest.TestCase):
    def test_codes_registry_is_complete(self):
        expected = {
            "PERMISSION_REQUIRED", "UNSUPPORTED_PLATFORM",
            "UNSUPPORTED_DISPLAY_SERVER", "BACKEND_UNAVAILABLE",
            "WINDOW_NOT_FOUND", "FOCUS_MISMATCH", "CAPTURE_FAILED",
            "INPUT_BLOCKED", "INVALID_TARGET", "RESOURCE_LIMIT",
            "OPERATION_TIMEOUT",
        }
        self.assertEqual(ERROR_CODES, frozenset(expected))

    def test_to_dict_shape_matches_roadmap(self):
        err = ApiError("PERMISSION_REQUIRED", "Screen recording permission is required",
                       status=403, platform="macos", action="screen_capture",
                       remediation="Open System Settings > Privacy & Security > Screen Recording")
        self.assertEqual(err.to_dict(), {
            "ok": False,
            "error": {
                "code": "PERMISSION_REQUIRED",
                "message": "Screen recording permission is required",
                "platform": "macos",
                "action": "screen_capture",
                "remediation": "Open System Settings > Privacy & Security > Screen Recording",
            },
        })

    def test_optional_fields_default_to_none(self):
        err = ApiError("CAPTURE_FAILED", "boom")
        d = err.to_dict()["error"]
        self.assertIsNone(d["platform"])
        self.assertIsNone(d["action"])
        self.assertIsNone(d["remediation"])

    def test_unknown_code_rejected(self):
        with self.assertRaises(ValueError):
            ApiError("NOT_A_REAL_CODE", "nope")

    def test_helper_constructors(self):
        cases = [
            (permission_required("screen_capture", "grant access"),
             "PERMISSION_REQUIRED", 403),
            (unsupported_platform("windows", "wayland_capture"),
             "UNSUPPORTED_DISPLAY_SERVER", 501),
            (window_not_found("hwnd=1234"),
             "WINDOW_NOT_FOUND", 404),
            (focus_mismatch("hwnd=1", "hwnd=2"),
             "FOCUS_MISMATCH", 409),
            (capture_failed("PrintWindow failed"),
             "CAPTURE_FAILED", 500),
            (invalid_target("pid 4 is a system process"),
             "INVALID_TARGET", 403),
        ]
        for err, code, status in cases:
            with self.subTest(code=code):
                self.assertIsInstance(err, ApiError)
                self.assertEqual(err.code, code)
                self.assertEqual(err.status, status)


if __name__ == "__main__":
    unittest.main()
