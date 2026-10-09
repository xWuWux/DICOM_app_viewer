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

# >= 32 chars: grading-api's app/config.py enforces a minimum
# coordinator-key length for any process importing the app (issue #97);
# kept in sync here so both suites share one convention even though this
# script only forwards the key as an HTTP header.
os.environ.setdefault(
    "GRADING_COORDINATOR_KEY", "test-only-coordinator-key-0123456789abcdef"
)

# provision-guacamole-session.py has a hyphen in its filename, so it can't
# be `import`ed normally -- load it by path instead, same technique the
# script itself would need if it were ever imported rather than run
# directly.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "provision-guacamole-session.py"
)
_spec = importlib.util.spec_from_file_location(
    "provision_guacamole_session", _MODULE_PATH
)
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
        result.stdout = ""  # issue #182: library helpers parse docker stdout
        return result

    with patch.object(provision.subprocess, "run", side_effect=_fake_run):
        yield calls


def _run_main(mock_docker_run, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["provision-guacamole-session.py", "--student-id", "STU_TEST"]
    )
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
        a
        for a in docker_run_args
        if isinstance(a, str) and a.startswith("VNC_PASSWORD=")
    ]
    assert len(vnc_password_env) == 1, (
        "expected exactly one -e VNC_PASSWORD=... argument"
    )
    password = vnc_password_env[0].split("=", 1)[1]
    assert len(password) > 0
    # Classic VNC/RFB auth only ever uses the first 8 bytes -- confirms
    # this is the deliberate token_hex(4) choice, not an accidental
    # longer secret whose extra length would just be silently ignored.
    assert len(password) == 8


def test_docker_run_uses_the_narrow_seccomp_profile_not_unconfined(
    mock_docker_run, monkeypatch, capsys
):
    """issue #52: `--security-opt seccomp=unconfined` exposed the full
    host kernel syscall surface. Guard against a regression back to it,
    and confirm the profile this actually points at is a real, existing
    file (not a typo'd path that would silently fall back to Docker's
    unconfined-if-file-missing... actually Docker just fails `docker run`
    outright on a missing profile path, but a real file is still the
    point: this profile has to be the one build-profile.py generates)."""
    docker_run_args = _run_main(mock_docker_run, monkeypatch)
    capsys.readouterr()

    security_opts = [
        a for a in docker_run_args if isinstance(a, str) and a.startswith("seccomp=")
    ]
    assert len(security_opts) == 1, "expected exactly one seccomp --security-opt"
    assert security_opts[0] != "seccomp=unconfined"

    profile_path = security_opts[0].split("=", 1)[1]
    assert os.path.isfile(profile_path), (
        f"seccomp profile path does not exist: {profile_path}"
    )
    assert os.path.basename(profile_path) == "profile.json"


def test_guacamole_connection_gets_the_same_vnc_password(
    mock_docker_run, monkeypatch, capsys
):
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
            monkeypatch.setattr(
                sys,
                "argv",
                ["provision-guacamole-session.py", "--student-id", "STU_TEST"],
            )
            provision.main()
    capsys.readouterr()

    docker_run_args = [c for c in mock_docker_run if c[:2] == ["docker", "run"]][0]
    vnc_password_env = next(
        a
        for a in docker_run_args
        if isinstance(a, str) and a.startswith("VNC_PASSWORD=")
    )
    container_password = vnc_password_env.split("=", 1)[1]

    assert captured_connection_data["parameters"]["password"] == container_password


# ---------------------------------------------------------------------------
# issue #182: mid-provision failure must leave NOTHING behind un-unwound
# (the compensation pattern create-session.py uses for the Kasm flow).
# Tests drive main(), so they cover the real __main__ contract:
# SystemExit(1) + reverse-order undos + honest stderr reporting.
# ---------------------------------------------------------------------------

import io as _io  # noqa: E402  (HTTPError body fp)


def _http_error(url):
    return provision.urllib.error.HTTPError(
        url, 500, "boom", {}, _io.BytesIO(b"upstream exploded")
    )


def _failing_urlopen(fail_on, seen):
    def _fake(req, timeout=15):
        seen.append((req.method, req.full_url))
        if fail_on and fail_on in req.full_url:
            raise _http_error(req.full_url)
        return _fake_urlopen(req, timeout=timeout)

    return _fake


def _container_name_from(mock_docker_run):
    run = [c for c in mock_docker_run if c[:2] == ["docker", "run"]][0]
    return run[run.index("--name") + 1]


def test_connection_create_failure_rolls_back_user_container_token(
    mock_docker_run, monkeypatch, capsys
):
    provision._UNDO.clear()
    monkeypatch.setattr(provision.gs, "sibling_containers", lambda *a, **k: [])
    seen = []
    monkeypatch.setattr(
        sys, "argv", ["provision-guacamole-session.py", "--student-id", "STU_RB"]
    )
    with patch.object(
        urllib.request,
        "urlopen",
        side_effect=_failing_urlopen("/connections?token=", seen),
    ):
        with pytest.raises(SystemExit) as stop:
            provision.main()
    assert stop.value.code == 1
    err = capsys.readouterr().err
    name = _container_name_from(mock_docker_run)
    # reverse order: user DELETE (cascade-covers permissions) came before...
    deletes = [u for m, u in seen if m == "DELETE" and "/users/stu_STU_RB" in u]
    assert len(deletes) == 2  # one pre-cleanup, ONE from the rollback
    # ...the container rm, which is the LAST docker call (the pre-run rm
    # for name-squatting long predates it), and then...
    assert mock_docker_run[-1] == ["docker", "rm", "-f", name]
    # ...the token revoke, which ran because there were no siblings.
    assert any("/api/session/revoke" in u for _, u in seen)
    for expected in (
        "undone: guacamole user",
        "undone: container",
        "undone: grading token",
    ):
        assert expected in err, expected


def test_rollback_skips_token_revoke_when_student_has_another_session(
    mock_docker_run, monkeypatch, capsys
):
    provision._UNDO.clear()
    monkeypatch.setattr(
        provision.gs, "sibling_containers", lambda *a, **k: ["guac-weasis-STU_SIB-111"]
    )
    seen = []
    monkeypatch.setattr(
        sys, "argv", ["provision-guacamole-session.py", "--student-id", "STU_SIB"]
    )
    with patch.object(
        urllib.request,
        "urlopen",
        side_effect=_failing_urlopen("/connections?token=", seen),
    ):
        with pytest.raises(SystemExit):
            provision.main()
    err = capsys.readouterr().err
    # revoke-by-student would kill the OTHER session's grading -- refused:
    assert not any("/api/session/revoke" in u for _, u in seen)
    assert "skipped token revoke" in err
    # everything per-SESSION was still undone:
    assert mock_docker_run[-1] == [
        "docker",
        "rm",
        "-f",
        _container_name_from(mock_docker_run),
    ]
    assert len([u for m, u in seen if m == "DELETE" and "/users/stu_STU_SIB" in u]) == 2


def test_docker_run_failure_leaves_no_orphan_token(
    mock_docker_run, monkeypatch, capsys
):
    provision._UNDO.clear()
    monkeypatch.setattr(provision.gs, "sibling_containers", lambda *a, **k: [])
    calls = mock_docker_run

    def _failing_run(args, **kwargs):
        calls.append(args)
        result = MagicMock()
        result.returncode = 1 if args[:2] == ["docker", "run"] else 0
        result.stderr = "image start failed" if result.returncode else ""
        result.stdout = ""
        return result

    seen = []
    monkeypatch.setattr(
        sys, "argv", ["provision-guacamole-session.py", "--student-id", "STU_RUN"]
    )
    with patch.object(provision.subprocess, "run", side_effect=_failing_run):
        with patch.object(
            urllib.request, "urlopen", side_effect=_failing_urlopen(None, seen)
        ):
            with pytest.raises(SystemExit) as stop:
                provision.main()
    assert stop.value.code == 1
    # the token minted BEFORE the failed run is revoked again...
    assert any("/api/session/revoke" in u for u in map(lambda t: t[1], seen))
    # ...and nothing Guacamole-side was ever created (we died before login).
    assert not any("/connections" in u or "/users?token" in u for _, u in seen)
    assert "docker run failed" in capsys.readouterr().err
