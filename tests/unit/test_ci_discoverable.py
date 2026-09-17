"""CI workflow must run unit tests on all three platforms and keep the
Windows live-server job single-step (runner lesson regression guard)."""
import unittest
from pathlib import Path

_WORKFLOW = (Path(__file__).resolve().parents[2]
             / ".github" / "workflows" / "test.yml")


class TestCIMatrix(unittest.TestCase):
    def setUp(self):
        self.text = _WORKFLOW.read_text(encoding="utf-8")

    def test_unit_job_has_three_os_matrix(self):
        self.assertIn("unit-tests", self.text)
        for os_name in ("ubuntu-latest", "macos-latest", "windows-latest"):
            self.assertIn(os_name, self.text)
        self.assertIn("matrix:", self.text)
        self.assertIn("unittest discover -s tests/unit", self.text)

    def test_live_server_job_kept_single_step(self):
        self.assertIn("Run security tests (server started in-step)", self.text)


if __name__ == "__main__":
    unittest.main()
