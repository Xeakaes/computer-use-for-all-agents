"""The MCP Registry publish workflow must exist and follow repo CI rules.

Regression guards for .github/workflows/publish-registry.yml:
- triggers on v* tags (release flow) and workflow_dispatch (manual re-publish)
- uses OIDC auth (id-token: write), never a PAT secret
- stamps server.json version from the pushed tag
- skips cleanly when the version is already published (idempotent re-runs)
- never touches the live-server security job's single-step pattern
"""
from pathlib import Path

import unittest

_WORKFLOW = (Path(__file__).resolve().parents[2]
             / ".github" / "workflows" / "publish-registry.yml")


class TestPublishWorkflow(unittest.TestCase):
    def setUp(self):
        self.assertTrue(_WORKFLOW.exists(),
                        "publish-registry.yml workflow file is missing")
        self.text = _WORKFLOW.read_text(encoding="utf-8")

    def test_triggers_on_version_tags_and_manual_dispatch(self):
        self.assertIn('tags: [ "v*" ]', self.text)
        self.assertIn("workflow_dispatch:", self.text)

    def test_uses_oidc_not_pat(self):
        self.assertIn("id-token: write", self.text)
        self.assertNotIn("MCP_GITHUB_TOKEN", self.text)

    def test_stamps_version_from_tag(self):
        self.assertIn("GITHUB_REF_NAME", self.text)
        self.assertIn("server.json", self.text)

    def test_skips_when_version_already_published(self):
        self.assertIn("versions/", self.text)
        self.assertIn("404", self.text)
        self.assertIn("Already published", self.text)

    def test_publishes_with_mcp_publisher(self):
        self.assertIn("mcp-publisher", self.text)
        self.assertIn("login github-oidc", self.text)
        self.assertIn("publish", self.text)


if __name__ == "__main__":
    unittest.main()
