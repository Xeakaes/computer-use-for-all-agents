"""backends.windows must be importable on every platform (import-time guard)."""
import unittest


class TestBackendImport(unittest.TestCase):
    def test_backends_windows_importable_on_this_platform(self):
        try:
            import backends.windows  # noqa: F401
        except NameError:
            self.fail("unguarded Windows-only symbol at import time")
        except ImportError as e:
            self.skipTest(f"optional platform dep missing: {e}")
        except KeyError as e:
            # pyautogui -> mouseinfo reads os.environ["DISPLAY"] on X11;
            # headless runners have pyautogui but no display.
            self.skipTest(f"environment cannot import pyautogui: {e}")


if __name__ == "__main__":
    unittest.main()
