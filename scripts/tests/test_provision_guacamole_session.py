"""
Unit tests for scripts/provision-guacamole-session.py (issue #53): the two
invariants that make this container's total lack of a published port /
VNC auth actually matter, verified directly rather than only by manual
inspection.

No Docker, no real Guacamole/grading-api needed -- subprocess.run and
urllib.request.urlopen are both mocked, so this runs in well under a
second and never touches real infrastructure (unlike
scripts/test-guacamole-integration.sh, which is the real end-to-end
proof this actually works against a live stack).
"""
import importlib.util
import json
import os
import sys
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GRADING_COORDINATOR_KEY", "test-only-coordinator-key")

# provision-guacamole-session.py has a hyphen in its filename, so it can't
# be `import`ed normally -- load it by path instead, same technique the
# script itself would need if it were ever imported rather than run
# directly.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "provision-guacamole-session.py"
)
_spec = importlib.util.spec_from_file_location("provision_guacamole_session", _MODULE_PATH)
provision = importlib.util.module_from_spec(_spec)
sys.modules["provision_guacamole_session"] = provision
_spec.loader.exec_module(provision)


class _FakeResponse:
    """Enough of urllib's response object for api_call()'s own
    read()+json.loads() to work against a canned payload."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(req, timeout=15):
    url = req.full_url
    if url.endswith("/api/session"):
        return _FakeResponse({"token": "fake-grading-token"})
    if url.endswith("/api/tokens"):
        return _FakeResponse({"authToken": "fake-admin-token"})
    if "/connections?token=" in url:
        return _FakeResponse({"identifier": "42"})
    # users create/delete, permissions grant -- none of these need a
    # meaningful body, main() never reads their return value.
    return _FakeResponse({})


@pytest.fixture
def mock_docker_run():
    """Captures every subprocess.run() call main() makes (docker rm -f,
    docker run) instead of actually invoking Docker."""
    calls = []

    def _fake_run(args, **kwargs):
        calls.append(args)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with patch.object(provision.subprocess, "run", side_effect=_fake_run):
        yield calls


def _run_main(mock_docker_run, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["provision-guacamole-session.py", "--student-id", "STU_TEST"])
    with patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        provision.main()
    docker_run_calls = [c for c in mock_docker_run if c[:2] == ["docker", "run"]]
    assert len(docker_run_calls) == 1, "expected exactly one `docker run` invocation"
    return docker_run_calls[0]


def test_docker_run_never_publishes_a_port(mock_docker_run, monkeypatch, capsys):
    """issue #53's own 'at minimum' ask: this container's VNC port must
    never be reachable from outside docker_network -- guard against a
    future -p/--publish regression, not just document the intent."""
    docker_run_args = _run_main(mock_docker_run, monkeypatch)
    capsys.readouterr()
    assert "-p" not in docker_run_args
    assert "--publish" not in docker_run_args


def test_docker_run_passes_a_real_vnc_password(mock_docker_run, monkeypatch, capsys):
    """issue #53's real fix: a per-session VNC_PASSWORD must reach the
    container's own environment, matching docker-entrypoint.sh's
    ${VNC_PASSWORD:?...} requirement."""
    docker_run_args = _run_main(mock_docker_run, monkeypatch)
    capsys.readouterr()
    vnc_password_env = [
        a for a in docker_run_args if isinstance(a, str) and a.startswith("VNC_PASSWORD=")
    ]
    assert len(vnc_password_env) == 1, "expected exactly one -e VNC_PASSWORD=... argument"
    password = vnc_password_env[0].split("=", 1)[1]
    assert len(password) > 0
    # Classic VNC/RFB auth only ever uses the first 8 bytes -- confirms
    # this is the deliberate token_hex(4) choice, not an accidental
    # longer secret whose extra length would just be silently ignored.
    assert len(password) == 8


def test_guacamole_connection_gets_the_same_vnc_password(mock_docker_run, monkeypatch, capsys):
    """The two ends of the VNC handshake must agree: whatever password the
    container's own VNC_PASSWORD env var got must be exactly what
    Guacamole's connection config sends back during authentication."""
    captured_connection_data = {}

    real_api_call = provision.api_call

    def _spying_api_call(method, url, data=None, **kwargs):
        if "/connections?token=" in url and method == "POST":
            captured_connection_data.update(data)
        return real_api_call(method, url, data=data, **kwargs)

    docker_run_args = None
    with patch.object(provision, "api_call", side_effect=_spying_api_call):
        with patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
            monkeypatch.setattr(sys, "argv", ["provision-guacamole-session.py", "--student-id", "STU_TEST"])
            provision.main()
    capsys.readouterr()

    docker_run_args = [c for c in mock_docker_run if c[:2] == ["docker", "run"]][0]
    vnc_password_env = next(
        a for a in docker_run_args if isinstance(a, str) and a.startswith("VNC_PASSWORD=")
    )
    container_password = vnc_password_env.split("=", 1)[1]

    assert captured_connection_data["parameters"]["password"] == container_password
