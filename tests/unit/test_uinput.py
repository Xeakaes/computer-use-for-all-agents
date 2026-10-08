"""Unit tests for the Linux uinput virtual device (ROADMAP Phase 5)."""
import os
import unittest

try:
    import evdev
except ImportError:
    evdev = None

from backends.uinput import (
    UInputDevice, button_to_code, key_to_code, uinput_permission_error,
)


class TestKeyToCode(unittest.TestCase):
    @unittest.skipUnless(evdev, "python-evdev is not importable here")
    def test_key_to_code_aliases(self):
        self.assertEqual(key_to_code("ctrl"), evdev.ecodes.KEY_LEFTCTRL)
        self.assertEqual(key_to_code("w"), evdev.ecodes.KEY_W)
        self.assertEqual(key_to_code("f4"), evdev.ecodes.KEY_F4)
        self.assertEqual(key_to_code("enter"), evdev.ecodes.KEY_ENTER)

    def test_key_to_code_unknown_raises(self):
        with self.assertRaises(KeyError):
            key_to_code("notakey")


class TestButtonToCode(unittest.TestCase):
    @unittest.skipUnless(evdev, "python-evdev is not importable here")
    def test_button_to_code(self):
        self.assertEqual(button_to_code("left"), evdev.ecodes.BTN_LEFT)
        self.assertEqual(button_to_code("right"), evdev.ecodes.BTN_RIGHT)
        self.assertEqual(button_to_code("middle"), evdev.ecodes.BTN_MIDDLE)


class TestPermissionError(unittest.TestCase):
    def test_permission_error_envelope(self):
        err = uinput_permission_error("game_start")
        self.assertEqual(err.code, "PERMISSION_REQUIRED")
        self.assertEqual(err.status, 403)
        self.assertIn("input group", err.remediation)
        self.assertEqual(err.remediation,
                         'add your user to the input group '
                         '(sudo usermod -aG input $USER, then re-login) '
                         'or install a udev rule: '
                         'KERNEL=="uinput", MODE="0660", GROUP="input"')


class TestUInputDevice(unittest.TestCase):
    @unittest.skipUnless(os.access("/dev/uinput", os.R_OK | os.W_OK),
                         "/dev/uinput not accessible")
    def test_uinput_device_roundtrip(self):
        device = UInputDevice()
        try:
            device.move_rel(3, -2)
            device.key_down("w")
            device.key_up("w")
        finally:
            device.close()


if __name__ == "__main__":
    unittest.main()
