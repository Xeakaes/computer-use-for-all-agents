"""Shared fixture: Flask test client wired to the FakeBackend (auth bypassed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backends.fake import FakeBackend          # noqa: E402
import core.backends as core_backends          # noqa: E402
import server                                  # noqa: E402

_TEST_TOKEN = "test-token"


class _AuthClient:
    """Test client that always sends a valid auth header."""

    def __init__(self, client):
        self._client = client

    def open(self, path, **kw):
        headers = kw.pop("headers", None) or {}
        headers.setdefault("X-Auth-Token", _TEST_TOKEN)
        return self._client.open(path, headers=headers, **kw)

    def get(self, path, **kw):
        return self.open(path, method="GET", **kw)

    def post(self, path, **kw):
        return self.open(path, method="POST", **kw)


def install_fake_backend():
    """Swap server + core.backends to a fresh FakeBackend. Returns it."""
    backend = FakeBackend()
    core_backends.set_backend(backend)
    server._backend = backend
    server._is_valid_token = lambda token: token == _TEST_TOKEN
    return backend


def make_fake_server():
    """Return (auth test client, FakeBackend). For function-style tests."""
    backend = install_fake_backend()
    return _AuthClient(server.app.test_client()), backend
