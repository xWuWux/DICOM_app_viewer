"""
Unit tests for scripts/create-session.py (issue #104): the two things that
made a failed run unsafe, verified directly rather than by inspection.

  1. no orphan token -- every failure after grading-api minted a token has to
     revoke it again, and the one failure where revoking would be WRONG (the
     Kasm session already exists and holds that token) has to leave it alone;
  2. TLS verification is what you get by default -- --insecure only ever
     disables it when someone types it explicitly, and a private CA has a
     real alternative in --ca-bundle instead of "disable checking".

No network, no Kasm, no grading-api: urllib.request.urlopen is mocked, so this
runs in well under a second and never touches real infrastructure (unlike
scripts/smoke-test.sh, which is the end-to-end check against the real stack).
"""
import importlib.util
import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

os.environ.setdefault("GRADING_COORDINATOR_KEY", "test-only-coordinator-key-0123456789abcdef")

# create-session.py has a hyphen in its filename, so it can't be `import`ed
# normally -- load it by path instead, same technique as
# test_provision_guacamole_session.py.
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "create-session.py")
_spec = importlib.util.spec_from_file_location("create_session", _MODULE_PATH)
create_session = importlib.util.module_from_spec(_spec)
sys.modules["create_session"] = create_session
_spec.loader.exec_module(create_session)

FAKE_TOKEN = "tok-" + "A" * 40
COORDINATOR_KEY = os.environ["GRADING_COORDINATOR_KEY"]


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


def _http_error(url, code=500, body=b"kasm is down"):
    return urllib.error.HTTPError(url, code, "Server Error", {}, io.BytesIO(body))


class _Kasm:
    """Canned Kasm + grading-api behaviour, and a record of what the script
    actually called. `kasm_mode`/`revoke_mode` are what individual tests
    flip to steer a run down a failure path."""

    def __init__(self, kasm_mode="ok", revoke_mode="ok", status_mode="running"):
        self.calls = []
        self.kasm_mode = kasm_mode
        self.revoke_mode = revoke_mode
        self.status_mode = status_mode

    def __call__(self, req, timeout=15, context=None):
        """`context` is recorded, not ignored: the happy-path test asserts
        every request went out over a verifying context, which is the
        end-to-end form of the TLS half of issue #104 (build_ssl_context()
        being correct in isolation does not prove main() actually uses it)."""
        url = req.full_url
        payload = json.loads(req.data.decode()) if req.data else {}
        self.calls.append((url, payload, dict(req.headers), context))
        if url.endswith("/api/session"):
            return _FakeResponse({"token": FAKE_TOKEN, "expires_at": 1})
        if url.endswith("/api/session/revoke"):
            if self.revoke_mode == "http_error":
                raise _http_error(url)
            return _FakeResponse({"revoked": self.revoke_mode == "ok", "count": 1 if self.revoke_mode == "ok" else 0})
        if url.endswith("/api/public/request_kasm"):
            if self.kasm_mode == "http_error":
                raise _http_error(url)
            if self.kasm_mode == "connection_error":
                raise urllib.error.URLError("connection refused")
            if self.kasm_mode == "no_kasm_id":
                return _FakeResponse({"user_id": "u1", "kasm_url": "/k/x"})
            return _FakeResponse({"kasm_id": "k1", "user_id": "u1", "kasm_url": "/k/x"})
        if url.endswith("/api/public/get_kasm_status"):
            if self.status_mode == "http_error":
                raise _http_error(url, code=403)
            if self.status_mode == "starting":
                # never reaches "running", so the polling loop gets to
                # time.sleep() -- where the Ctrl-C test injects itself.
                return _FakeResponse({"kasm": {"operational_status": "building"}, "operational_progress": 40})
            return _FakeResponse({"kasm": {"operational_status": "running"}})
        raise AssertionError(f"unexpected url {url}")

    def called(self, suffix):
        """Payloads the script POSTed to the endpoint ending in `suffix`."""
        return [payload for url, payload, _, _ in self.calls if url.endswith(suffix)]

    def headers_to(self, suffix):
        return [headers for url, _, headers, _ in self.calls if url.endswith(suffix)]


@pytest.fixture
def env(monkeypatch):
    """Everything main() reads from the environment, so no test can be
    affected by a real KASM_* value in the developer's shell."""
    for name, value in {
        "KASM_SERVER": "https://kasm.test",
        "KASM_API_KEY": "key",
        "KASM_API_KEY_SECRET": "secret",
        "KASM_IMAGE_ID": "img",
        "GRADING_COORDINATOR_KEY": COORDINATOR_KEY,
        "GRADING_API_URL": "http://localhost:8080/",
    }.items():
        monkeypatch.setenv(name, value)


def _run(monkeypatch, kasm, *extra_argv):
    """Runs main() against the canned backend. Returns the SystemExit raised
    by a failing run, or None when the run completed and printed its link."""
    monkeypatch.setattr(sys, "argv", ["create-session.py", "--student-id", "STU_TEST", *extra_argv])
    with patch.object(urllib.request, "urlopen", side_effect=kasm):
        try:
            create_session.main()
        except SystemExit as exc:
            return exc
    return None


def test_happy_path_prints_the_link_and_never_revokes(env, monkeypatch, capsys):
    """The healthy run must stay byte-for-byte as it was before issue #104 --
    the compensation added here must not start firing on success."""
    kasm = _Kasm()
    assert _run(monkeypatch, kasm) is None

    printed = json.loads(capsys.readouterr().out)
    assert printed["kasm_id"] == "k1"
    assert printed["link"] == "https://kasm.test/k/x"
    assert printed["ready"] is True
    assert kasm.called("/api/session/revoke") == []
    # every request that went out did so on a verifying context (issue #104:
    # the default must be verification, for Kasm and grading-api alike)
    assert kasm.calls, "the happy path made no requests at all"
    for _, _, _, ctx in kasm.calls:
        assert ctx is not None and ctx.verify_mode is ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


def test_kasm_http_failure_revokes_the_minted_token(env, monkeypatch, capsys):
    """issue #104's core: request_kasm fails -> the token minted seconds
    earlier must not be left alive."""
    kasm = _Kasm(kasm_mode="http_error")
    exit_code = _run(monkeypatch, kasm)
    assert exit_code is not None and exit_code.code != 0

    revoked = kasm.called("/api/session/revoke")
    assert revoked == [{"token": FAKE_TOKEN}]
    assert kasm.headers_to("/api/session/revoke")[0]["X-coordinator-key"] == COORDINATOR_KEY
    assert "compensated" in capsys.readouterr().err


def test_connection_failure_to_kasm_revokes_too(env, monkeypatch):
    """Not just an HTTP 500: a refused connection raises URLError, which is a
    different exception class and used to escape even further (straight out
    of the script, no handler at all)."""
    kasm = _Kasm(kasm_mode="connection_error")
    assert _run(monkeypatch, kasm) is not None
    assert kasm.called("/api/session/revoke") == [{"token": FAKE_TOKEN}]


def test_unexpected_kasm_response_without_kasm_id_revokes(env, monkeypatch):
    """A 200 that turns out to carry no kasm_id is a failure too -- it used to
    sys.exit() with the token already minted and nothing undone."""
    kasm = _Kasm(kasm_mode="no_kasm_id")
    assert _run(monkeypatch, kasm) is not None
    assert kasm.called("/api/session/revoke") == [{"token": FAKE_TOKEN}]


def test_interrupted_after_kasm_exists_does_not_revoke(env, monkeypatch):
    """The other half of the invariant: once request_kasm answered, the token
    lives inside a real container's environment. Ctrl-C at that point must
    exit without revoking -- revoking would break a session a student may be
    joining right now, which is worse than the token's own remaining TTL."""
    kasm = _Kasm(status_mode="starting")
    monkeypatch.setattr(create_session.time, "sleep", _raise_keyboard_interrupt)
    assert _run(monkeypatch, kasm) is not None
    assert kasm.called("/api/session/revoke") == []
    # and the session itself is left alone, ready for the student
    assert kasm.called("/api/public/request_kasm")


def _raise_keyboard_interrupt(*args):
    raise KeyboardInterrupt


def test_failed_revoke_warns_without_leaking_the_token(env, monkeypatch, capsys):
    """If the compensation itself fails there is nothing left but telling a
    human -- and the warning must identify the session by student_id, never
    by the credential. 2026-09-22 already had the incident where long-lived
    secrets ended up in a committed transcript; this script's stderr goes to
    shell history and CI logs the same way."""
    kasm = _Kasm(kasm_mode="http_error", revoke_mode="http_error")
    assert _run(monkeypatch, kasm) is not None
    captured = capsys.readouterr()
    assert "NOT revoked" in captured.err
    assert "STU_TEST" in captured.err
    assert FAKE_TOKEN not in captured.err + captured.out


def test_tls_is_verified_by_default(env):
    """The default is the whole fix: no flag, no env var, verification on."""
    ctx = create_session.build_ssl_context()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_ca_bundle_keeps_verification_on_and_uses_that_ca(env, tmp_path):
    """--ca-bundle is the acceptance criterion's "CA przez parametr": a
    private CA gets a supported path that is not "stop verifying". Skipped
    where the box has no readable trust store to point it at."""
    ca_file = ssl.get_default_verify_paths().cafile
    if not ca_file or not os.path.isfile(ca_file):
        pytest.skip("no system CA bundle on this box to use as a fixture")
    ctx = create_session.build_ssl_context(ca_file)
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.get_ca_certs(), "--ca-bundle must actually load the named CA file"


def test_insecure_still_works_but_shouts(env, capsys):
    ctx = create_session.build_ssl_context(None, insecure=True)
    assert ctx.verify_mode is ssl.CERT_NONE
    assert ctx.check_hostname is False
    err = capsys.readouterr().err
    assert "WARNING" in err and "--ca-bundle" in err


def test_insecure_and_ca_bundle_are_mutually_exclusive(env, monkeypatch, capsys):
    """Silently ignoring --ca-bundle because --insecure was also typed would
    leave the operator believing their CA was in use."""
    kasm = _Kasm()
    exit_code = _run(monkeypatch, kasm, "--insecure", "--ca-bundle", "/nonexistent/ca.pem")
    assert exit_code is not None and exit_code.code == 2
    assert kasm.calls == []  # rejected before any request went out


def test_missing_ca_bundle_fails_before_any_request(env, monkeypatch, tmp_path):
    exit_code = _run(monkeypatch, _Kasm(), "--ca-bundle", str(tmp_path / "absent.pem"))
    assert exit_code is not None and exit_code.code == 2
