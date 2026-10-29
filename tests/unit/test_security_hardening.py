"""Pentest remediation contracts (strix audit 2026-10-29).

Guards for the fixes applied from penetration_test_report.md:
- VULN-001: index.html builds window rows via DOM APIs + ships a CSP
- VULN-002: /token refuses non-loopback peers (Host header is not identity)
- VULN-003/006: .token/.apikeys are 0600, written atomically, after bind
- VULN-004: canonical forbidden-key policy (Super_L/Alt_L/VK digit bypass)
- VULN-005: start-server.bat pins cloudflared, tunnel is opt-in
- VULN-007: /api/launch refuses interpreter/executor app names
- VULN-014/015: MCP guard constant-time compare + unified expiry semantics
- VULN-016: README no longer documents the rejected ?token= URL form
- VULN-022: workflows pin actions to commit SHAs
- VULN-023: scoped keys carry scopes; admin endpoints are master-only
"""
import os
import re
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import server  # noqa: E402
from backends.forbidden import (assert_forbidden,  # noqa: E402
                                canonical_policy_keys)
from tests.unit import fakes  # noqa: E402
from tests.unit.fakes import make_fake_server  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]


class TestCanonicalForbiddenPolicy(unittest.TestCase):
    """VULN-004: policy must match on canonical key identity, not raw names."""

    def test_modifier_keysym_variants_blocked(self):
        for name in ("Super_L", "Super_R", "Meta_L", "Meta_R",
                     "LeftWin", "RightWin", "super", "meta"):
            with self.subTest(name=name):
                with self.assertRaises(PermissionError):
                    assert_forbidden([name])

    def test_alt_l_f4_chord_blocked(self):
        with self.assertRaises(PermissionError):
            assert_forbidden(["Alt_L", "F4"])
        with self.assertRaises(PermissionError):
            assert_forbidden(["alt_l", "f4"])

    def test_super_l_chords_blocked(self):
        with self.assertRaises(PermissionError):
            assert_forbidden(["Super_L", "l"])
        with self.assertRaises(PermissionError):
            assert_forbidden(["Meta_L", "d"])

    def test_control_and_delete_variants_blocked(self):
        with self.assertRaises(PermissionError):
            assert_forbidden(["Control_L", "Alt_L", "Delete"])
        with self.assertRaises(PermissionError):
            assert_forbidden(["KP_Delete"])

    def test_plain_keys_and_allowed_chords_unchanged(self):
        assert_forbidden(["a"])
        assert_forbidden(["ctrl", "shift", "esc"])   # Task Manager allowed
        assert_forbidden(["alt", "tab"])
        assert_forbidden(["ctrl", "c"])


class TestWindowsVKPolicyNames(unittest.TestCase):
    """VULN-004: raw VK digit strings must resolve to canonical policy names."""

    @staticmethod
    def _vk(name):
        return int(name) if str(name).isdigit() else None

    def test_vk_digit_bypasses_resolved(self):
        self.assertEqual(canonical_policy_keys(["91"], self._vk), ["win"])
        self.assertEqual(canonical_policy_keys(["92"], self._vk), ["win"])
        self.assertEqual(canonical_policy_keys(["46"], self._vk), ["delete"])
        self.assertEqual(canonical_policy_keys(["18", "115"], self._vk),
                         ["alt", "f4"])

    def test_harmless_digits_pass_through(self):
        self.assertEqual(canonical_policy_keys(["65"], self._vk), ["65"])
        self.assertEqual(canonical_policy_keys(["13"], self._vk), ["13"])

    def test_resolved_digits_hit_the_policy(self):
        with self.assertRaises(PermissionError):
            assert_forbidden(canonical_policy_keys(["91"], self._vk))
        with self.assertRaises(PermissionError):
            assert_forbidden(canonical_policy_keys(["18", "115"], self._vk))


class TestTokenFileHygiene(unittest.TestCase):
    """VULN-003/006: private files are 0600, atomic, published after bind."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token_path = os.path.join(self.tmp.name, ".token")
        self.apikeys_path = os.path.join(self.tmp.name, ".apikeys")
        self._saved_token_file = server.SESSION_TOKEN_FILE
        self._saved_apikeys_file = server.APIKEYS_FILE
        server.SESSION_TOKEN_FILE = self.token_path
        server.APIKEYS_FILE = self.apikeys_path
        self.addCleanup(self._restore)

    def _restore(self):
        server.SESSION_TOKEN_FILE = self._saved_token_file
        server.APIKEYS_FILE = self._saved_apikeys_file

    def _mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_write_session_token_is_0600_and_atomic(self):
        server._write_session_token()
        self.assertTrue(os.path.exists(self.token_path))
        self.assertEqual(self._mode(self.token_path), 0o600)
        with open(self.token_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), server.SESSION_TOKEN)
        self.assertFalse(os.path.exists(self.token_path + ".tmp"))

    def test_save_api_keys_is_0600_and_atomic(self):
        server._api_key_names["a" * 64] = "unit-key"
        self.addCleanup(server._api_key_names.pop, "a" * 64, None)
        server._save_api_keys()
        self.assertEqual(self._mode(self.apikeys_path), 0o600)
        self.assertFalse(os.path.exists(self.apikeys_path + ".tmp"))

    def test_module_no_longer_writes_token_at_import_time(self):
        src = (_REPO / "server.py").read_text(encoding="utf-8")
        self.assertNotIn('with open(SESSION_TOKEN_FILE, "w"', src)
        self.assertIn("def _write_session_token", src)

    def test_main_publishes_token_only_after_bind(self):
        src = (_REPO / "server.py").read_text(encoding="utf-8")
        main_src = src[src.index("def main():"):]
        self.assertLess(main_src.index("make_server"),
                        main_src.index("_write_session_token()"),
                        "token must be published after the socket binds")
        self.assertIn("release_all()", main_src)  # B12: no stuck keys on exit


class TestTokenEndpointPeerGate(unittest.TestCase):
    """VULN-002: /token keys off the real peer address, not a client header."""

    def test_loopback_peer_still_works(self):
        client = server.app.test_client()
        r = client.get("/token", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get("ok"))

    def test_remote_peer_refused_even_with_loopback_host(self):
        client = server.app.test_client()
        r = client.get("/token",
                       base_url="http://127.0.0.1:8745",
                       environ_base={"REMOTE_ADDR": "192.168.1.50"})
        self.assertEqual(r.status_code, 403)
        body = r.get_json()
        self.assertFalse(body.get("ok"))

    def test_remote_peer_still_refused_on_foreign_host(self):
        client = server.app.test_client()
        r = client.get("/token",
                       base_url="http://rebind.attacker.invalid:8745",
                       environ_base={"REMOTE_ADDR": "192.168.1.50"})
        self.assertEqual(r.status_code, 421)


class TestLaunchPolicy(unittest.TestCase):
    """VULN-007: interpreters/executors must not be launchable with args."""

    def setUp(self):
        self.client, self.backend = make_fake_server()

    def test_interpreters_refused(self):
        for app in ("python3", "python", "python3.12", "node", "bash", "sh",
                    "powershell", "pwsh", "cmd", "ruby", "perl", "php"):
            with self.subTest(app=app):
                r = self.client.post("/api/launch", json={"app": app})
                self.assertEqual(r.status_code, 400)
                self.assertIn("not permitted", r.get_json().get("error", ""))

    def test_executors_refused(self):
        for app in ("env", "xargs", "find", "busybox", "timeout", "nohup",
                    "sudo", "doas", "wscript", "cscript", "mshta",
                    "rundll32", "regsvr32", "certutil", "curl", "wget"):
            with self.subTest(app=app):
                r = self.client.post("/api/launch", json={"app": app})
                self.assertEqual(r.status_code, 400)

    def test_unknown_normal_app_still_not_found(self):
        r = self.client.post("/api/launch",
                             json={"app": "definitely-not-an-app-918273"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not found", r.get_json().get("error", ""))


class TestKeyScopesAndAdminEndpoints(unittest.TestCase):
    """VULN-023: scoped keys carry scopes; admin endpoints are master-only."""

    def setUp(self):
        self.client, self.backend = make_fake_server()
        server._is_valid_token = fakes._REAL_IS_VALID_TOKEN
        self.addCleanup(setattr, server, "_is_valid_token",
                        lambda token: token == "test-token")
        self.master = {"X-Auth-Token": server.SESSION_TOKEN}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = server.APIKEYS_FILE
        server.APIKEYS_FILE = os.path.join(self.tmp.name, ".apikeys")
        self.addCleanup(setattr, server, "APIKEYS_FILE", self._saved)
        server._persistent_keys.clear()
        server._api_key_names.clear()
        server._api_key_expiry.clear()
        if hasattr(server, "_api_key_scopes"):
            server._api_key_scopes.clear()
        self.addCleanup(server._persistent_keys.clear)
        self.addCleanup(server._api_key_names.clear)
        self.addCleanup(server._api_key_expiry.clear)

    def _create(self, name, **extra):
        body = {"action": "create", "name": name, **extra}
        r = self.client.post("/api/keys", json=body, headers=self.master)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["key"]

    def test_rest_only_key_cannot_touch_admin_endpoints(self):
        key = self._create("rest-only", scopes=["rest"])
        h = {"X-Auth-Token": key}
        self.assertEqual(self.client.get("/api/info", headers=h).status_code,
                         200)
        self.assertEqual(
            self.client.get("/api/session", headers=h).status_code, 403)
        self.assertEqual(
            self.client.post("/api/keys", json={"action": "list"},
                             headers=h).status_code, 403)
        self.assertEqual(
            self.client.post("/api/session/clear", json={},
                             headers=h).status_code, 403)

    def test_mcp_only_key_refused_on_rest(self):
        key = self._create("mcp-only", scopes=["mcp"])
        r = self.client.get("/api/info", headers={"X-Auth-Token": key})
        self.assertEqual(r.status_code, 403)

    def test_legacy_key_keeps_rest_but_not_admin(self):
        key = self._create("legacy")
        h = {"X-Auth-Token": key}
        self.assertEqual(self.client.get("/api/info", headers=h).status_code,
                         200)
        self.assertEqual(
            self.client.get("/api/session", headers=h).status_code, 403)

    def test_master_keeps_full_access(self):
        self.assertEqual(
            self.client.get("/api/session", headers=self.master).status_code,
            200)
        r = self.client.post("/api/keys", json={"action": "list"},
                             headers=self.master)
        self.assertEqual(r.status_code, 200)

    def test_scopes_survive_save_and_reload(self):
        self._create("persist", scopes=["mcp"])
        server._persistent_keys.clear()
        server._api_key_names.clear()
        server._api_key_expiry.clear()
        server._api_key_scopes.clear()
        server._load_api_keys()
        self.assertEqual(len(server._persistent_keys), 1)
        scopes = next(iter(server._api_key_scopes.values()))
        self.assertEqual(scopes, {"mcp"})

    def test_invalid_scope_rejected(self):
        r = self.client.post("/api/keys",
                             json={"action": "create", "name": "bad",
                                   "scopes": ["everything"]},
                             headers=self.master)
        self.assertEqual(r.status_code, 400)


class TestMcpGuardHelpers(unittest.TestCase):
    """VULN-014/015: constant-time compare, unified expiry, scope on URL keys."""

    def setUp(self):
        try:
            import mcp_server
        except ImportError as exc:  # pragma: no cover - mcp SDK optional here
            self.skipTest(f"mcp SDK unavailable: {exc}")
        self.mcp = mcp_server
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved_file = mcp_server._APIKEYS_FILE
        self._saved_token_env = os.environ.get("SCREEN_CONTROL_TOKEN")
        os.environ["SCREEN_CONTROL_TOKEN"] = "0" * 48
        self.addCleanup(self._restore)

    def _restore(self):
        self.mcp._APIKEYS_FILE = self._saved_file
        if self._saved_token_env is None:
            os.environ.pop("SCREEN_CONTROL_TOKEN", None)
        else:
            os.environ["SCREEN_CONTROL_TOKEN"] = self._saved_token_env

    def _write_apikeys(self, lines):
        path = os.path.join(self.tmp.name, ".apikeys")
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.mcp._APIKEYS_FILE = Path(path)
        self.mcp._scoped_key_hashes._cache = {"mtime": None, "keys": {}}

    def test_master_token_rejected_in_url_path(self):
        self.assertEqual(self.mcp._url_key_state("0" * 48), "master")

    def test_unknown_key_state(self):
        self._write_apikeys([])
        self.assertEqual(self.mcp._url_key_state("a" * 48), "unknown")

    def test_no_expiry_key_is_valid_not_unknown(self):
        import hashlib
        key = "b" * 48
        kh = hashlib.sha256(key.encode()).hexdigest()
        self._write_apikeys([f"{kh}\tno-exp"])
        self.assertEqual(self.mcp._url_key_state(key), "ok")

    def test_expired_key_state(self):
        import hashlib
        key = "c" * 48
        kh = hashlib.sha256(key.encode()).hexdigest()
        past = time.time() - 60
        self._write_apikeys([f"{kh}\told\t{past}"])
        self.assertEqual(self.mcp._url_key_state(key), "expired")

    def test_rest_only_key_refused_on_mcp_url(self):
        import hashlib
        key = "d" * 48
        kh = hashlib.sha256(key.encode()).hexdigest()
        future = time.time() + 3600
        self._write_apikeys([f"{kh}\trest-only\t{future}\trest"])
        self.assertEqual(self.mcp._url_key_state(key), "no-mcp-scope")

    def test_header_compare_is_constant_time_and_null_safe(self):
        self.assertFalse(self.mcp._header_token_valid(None))
        self.assertFalse(self.mcp._header_token_valid(""))
        self.assertFalse(self.mcp._header_token_valid("f" * 48))
        self.assertTrue(self.mcp._header_token_valid("0" * 48))


class TestStaticArtifactGuards(unittest.TestCase):
    """VULN-001/005/016/022: shipped artifacts carry the hardening."""

    def test_index_html_uses_dom_api_and_csp(self):
        text = ( _REPO / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("${w.title", text)
        self.assertNotIn("${w.process", text)
        self.assertIn("script-src-attr 'none'", text)

    def test_server_ships_csp_header(self):
        src = (_REPO / "server.py").read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", src)

    def test_start_server_bat_pins_and_optimizes_tunnel(self):
        text = (_REPO / "start-server.bat").read_text(encoding="utf-8")
        self.assertNotIn("releases/latest", text)
        self.assertIn("CLOUDFLARED_VER=", text)
        self.assertIn("--tunnel", text)
        self.assertIn("certutil", text)

    def test_readme_no_longer_documents_url_token_form(self):
        text = (_REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("URL form", text)
        self.assertNotIn("?token=<token>", text)

    def test_workflows_pin_actions_to_shas(self):
        for wf in (_REPO / ".github" / "workflows").glob("*.yml"):
            for line in wf.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("uses:"):
                    with self.subTest(workflow=wf.name, uses=stripped):
                        self.assertRegex(
                            stripped,
                            r"^uses:\s+\S+@[0-9a-f]{40}\s*(#.*)?$",
                            f"unpinned action in {wf.name}")


if __name__ == "__main__":
    unittest.main()
