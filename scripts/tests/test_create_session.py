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
import signal
import ssl
import subprocess
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

    def __init__(self, kasm_mode="ok", revoke_mode="ok", status_mode="running",
                 mint_mode="ok", filler=""):
        self.calls = []
        self.kasm_mode = kasm_mode
        self.revoke_mode = revoke_mode
        self.status_mode = status_mode
        self.mint_mode = mint_mode
        self.filler = filler
        self.handler_state = "never asked"

    def __call__(self, req, timeout=15, context=None):
        """`context` is recorded, not ignored: the happy-path test asserts
        every request went out over a verifying context, which is the
        end-to-end form of the TLS half of issue #104 (build_ssl_context()
        being correct in isolation does not prove main() actually uses it)."""
        url = req.full_url
        payload = json.loads(req.data.decode()) if req.data else {}
        self.calls.append((url, payload, dict(req.headers), context))
        if url.endswith("/api/session"):
            if self.mint_mode == "timeout":
                # The review nit #3 case: the request went out, the server may
                # or may not have committed, and nothing here knows which.
                raise urllib.error.URLError("timed out")
            if self.mint_mode.startswith("http_"):
                raise _http_error(url, code=int(self.mint_mode.split("_")[1]))
            if self.mint_mode == "no_token":
                return _FakeResponse({"expires_at": 1})
            return _FakeResponse({"token": FAKE_TOKEN, "expires_at": 1})
        if url.endswith("/api/session/revoke"):
            if self.revoke_mode == "http_error":
                raise _http_error(url)
            if self.revoke_mode == "interrupt":
                raise KeyboardInterrupt()
            return _FakeResponse({"revoked": self.revoke_mode == "ok", "count": 1 if self.revoke_mode == "ok" else 0})
        if url.endswith("/api/public/request_kasm"):
            if self.kasm_mode == "http_error":
                raise _http_error(url)
            if self.kasm_mode == "connection_error":
                raise urllib.error.URLError("connection refused")
            if self.kasm_mode == "sigterm_during_request":
                # The handler's own effect, raised at the interrupted point.
                create_session._interrupt(signal.SIGTERM, None)
            if self.kasm_mode == "raise_signal":
                # The real thing: an actual signal delivered to this process,
                # translated by whatever handler is installed at that moment.
                # Delivered ONLY when our handler is actually in place --
                # otherwise a removed install call would SIGTERM the pytest
                # process itself, and a killed runner is a worse signal than a
                # failed assertion.
                if signal.getsignal(signal.SIGTERM) is not create_session._interrupt:
                    self.handler_state = "not installed"
                    return _FakeResponse({"kasm_id": "k1", "user_id": "u1", "kasm_url": "/k/x"})
                self.handler_state = "installed"
                signal.raise_signal(signal.SIGTERM)
                raise urllib.error.URLError("interrupted")
            if self.kasm_mode == "no_kasm_id":
                return _FakeResponse({"user_id": "u1", "kasm_url": "/k/x", "filler": self.filler})
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
    """Runs main() against the canned backend. Returns whatever escaped it --
    normally a SystemExit, but BaseException is caught deliberately: an
    exception leaking out of main() is itself the bug some tests below are
    looking for (compensation skipped), and it should fail an assertion here
    rather than abort the whole pytest session."""
    monkeypatch.setattr(sys, "argv", ["create-session.py", "--student-id", "STU_TEST", *extra_argv])
    with patch.object(urllib.request, "urlopen", side_effect=kasm):
        try:
            create_session.main()
        except BaseException as exc:
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


# ---- review nits (CR w4:p1): what can kill the process besides Ctrl-C ----


@pytest.fixture(autouse=True)
def _restore_signals():
    """main() installs real signal handlers; without this they'd leak into
    every later test in the session (and into pytest's own Ctrl-C handling)."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM))}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_sigterm_triggers_the_same_compensation_as_ctrl_c(env, monkeypatch):
    """CI job cancellation and a closed terminal are how this script actually
    gets killed in practice. Before the review nit was fixed, SIGTERM took the
    default action -- process gone, token alive, nothing printed."""
    kasm = _Kasm(kasm_mode="sigterm_during_request")
    exit_code = _run(monkeypatch, kasm)
    assert exit_code is not None
    assert exit_code.code == "interrupted"
    assert kasm.called("/api/session/revoke") == [{"token": FAKE_TOKEN}]


def test_sigterm_handler_maps_to_keyboard_interrupt(env):
    create_session.install_termination_handlers()
    assert signal.getsignal(signal.SIGTERM) is create_session._interrupt
    with pytest.raises(KeyboardInterrupt):
        create_session._interrupt(signal.SIGTERM, None)


@pytest.mark.parametrize("sig_name", ["SIGTERM", "SIGHUP"])
def test_both_signals_are_installed_and_really_delivered(env, sig_name):
    """N4 + the reviewer's point (c): installing a handler is not the same as
    the OS reaching it. raise_signal delivers for real, in-process, on the
    pytest main thread -- the autouse fixture puts the previous handler back."""
    sig = getattr(signal, sig_name, None)
    if sig is None:
        pytest.skip(f"{sig_name} does not exist on this platform")
    signal.signal(sig, signal.SIG_DFL)
    create_session.install_termination_handlers()
    assert signal.getsignal(sig) is create_session._interrupt
    with pytest.raises(KeyboardInterrupt):
        signal.raise_signal(sig)


def test_inherited_sig_ign_is_left_alone(env):
    """Nit N1: `nohup` / `trap '' HUP` means "ignore HUP". Overriding it would
    turn a run that used to outlive its terminal into one that aborts mid-way --
    a regression introduced by adding the handler in the first place."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    create_session.install_termination_handlers()
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN


def test_compensate_runs_when_sigterm_lands_after_the_mint(env, monkeypatch, capsys):
    """Real end-to-end of the handler: request_kasm delivers SIGTERM to this
    process mid-request, the installed handler turns it into
    KeyboardInterrupt at that point, and the minted token gets revoked.
    Delivery is via raise_signal, so this proves wiring, not just mapping."""
    kasm = _Kasm(kasm_mode="raise_signal")
    exit_code = _run(monkeypatch, kasm)
    assert kasm.handler_state == "installed", "main() must install the handlers before the first request"
    assert isinstance(exit_code, SystemExit)
    assert kasm.called("/api/session/revoke") == [{"token": FAKE_TOKEN}]


def test_main_installs_the_handlers_before_minting(env, monkeypatch):
    """The mapping above is worthless if nothing installs it, and installing it
    after POST /session would be worthless too -- the window that matters is the
    mint-then-Kasm one. A spy, so removing the call fails a test instead of
    killing the test runner with a real SIGTERM."""
    kasm = _Kasm()
    seen = {}

    def spy():
        seen["requests_already_made"] = len(kasm.calls)

    monkeypatch.setattr(create_session, "install_termination_handlers", spy)
    _run(monkeypatch, kasm)
    assert seen == {"requests_already_made": 0}, "handlers must be installed once, before any request"


def test_ctrl_c_during_the_revoke_call_still_reports(env, monkeypatch, capsys):
    """The second Ctrl-C is the one that used to escape the compensation with a
    traceback and the token still live -- so the outcome here must still be a
    clean SystemExit plus the manual-undo hint, not a leaked KeyboardInterrupt."""
    kasm = _Kasm(kasm_mode="http_error", revoke_mode="interrupt")
    exit_code = _run(monkeypatch, kasm)
    assert isinstance(exit_code, SystemExit), f"compensation leaked {exit_code!r}"
    err = capsys.readouterr().err
    assert "NOT revoked" in err
    assert FAKE_TOKEN not in err


def test_ambiguous_mint_hinted_but_never_auto_revoked(env, monkeypatch, capsys):
    """POST /session answered with something that carries no token. The mint
    may or may not have committed, and it deletes that student_id's previous
    token either way -- so revoking by student_id from here could destroy a
    live session nothing failed to create. Hint, don't act."""
    kasm = _Kasm(mint_mode="no_token")
    assert _run(monkeypatch, kasm) is not None
    err = capsys.readouterr().err
    assert "did not return a token" in err and "STU_TEST" in err
    assert kasm.called("/api/session/revoke") == []
    assert FAKE_TOKEN not in err


def test_mint_timeout_gets_the_same_ambiguous_hint(env, monkeypatch, capsys):
    kasm = _Kasm(mint_mode="timeout")
    assert _run(monkeypatch, kasm) is not None
    assert "did not return a token" in capsys.readouterr().err
    assert kasm.called("/api/session/revoke") == []


@pytest.mark.parametrize("mint_mode,expect_hint", [
    ("http_401", False),   # nit N2: a definite refusal minted nothing
    ("http_403", False),
    ("http_422", False),
    ("http_500", True),    # the server may have committed before it fell over
])
def test_ambiguous_hint_only_for_ambiguous_mint_failures(env, monkeypatch, capsys, mint_mode, expect_hint):
    """A wrong coordinator key is the most common failure this script sees.
    Answering it with a `!! go hunt for a token` hint would train operators to
    ignore the hint when it is real."""
    kasm = _Kasm(mint_mode=mint_mode)
    assert _run(monkeypatch, kasm) is not None
    hinted = "did not return a token" in capsys.readouterr().err
    assert hinted is expect_hint
    assert kasm.called("/api/session/revoke") == []


def test_no_kasm_id_error_text_is_bounded(env, monkeypatch, capsys):
    """_MAX_ERROR_BODY_CHARS claims error text is capped; the one place that
    dumps a whole response body has to honour that claim (a fat 200 from
    request_kasm would otherwise go to the terminal/log in full)."""
    kasm = _Kasm(kasm_mode="no_kasm_id", filler="x" * 4000)
    exit_code = _run(monkeypatch, kasm)
    err = capsys.readouterr().err
    assert exit_code is not None
    assert len(str(exit_code.code)) < 600, "error text grew past the declared cap"
    assert err.count("x") <= create_session._MAX_ERROR_BODY_CHARS


# ---- the one-context-for-two-hosts nit ----


def test_ca_bundle_adds_to_the_system_store_instead_of_replacing_it(env, tmp_path):
    """Kasm's gateway and grading-api's proxy are routinely two different CAs.
    create_default_context(cafile=...) would replace the whole store and break
    the other host, which is a silently-broken-deployment footgun."""
    ca_file = ssl.get_default_verify_paths().cafile
    if not ca_file or not os.path.isfile(ca_file):
        pytest.skip("no system CA bundle on this box to use as a fixture")
    private_ca = tmp_path / "private-ca.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=IP_CMC test private CA", "-keyout", str(tmp_path / "k.pem"),
         "-out", str(private_ca)],
        check=True, capture_output=True,
    )
    base = create_session.build_ssl_context()
    ctx = create_session.build_ssl_context(str(private_ca))
    subjects = {c["subject"][0][0][1] for c in ctx.get_ca_certs()}
    assert "IP_CMC test private CA" in subjects, "--ca-bundle must be trusted"
    assert len(ctx.get_ca_certs()) > len(base.get_ca_certs()), "system store must stay loaded too"
    assert ctx.verify_mode is ssl.CERT_REQUIRED and ctx.check_hostname is True


# ---- the cleartext-coordinator-key nit ----


@pytest.mark.parametrize("url,should_warn", [
    ("http://localhost:8080/", False),      # the documented default deployment
    ("http://127.0.0.1:8080/", False),
    ("http://127.0.0.2:8080/", False),      # nit N3: whole 127/8 is loopback
    ("http://[::1]:8080/", False),
    ("http://0.0.0.0:8080/", False),
    ("https://grading.example/", False),
    ("HTTP://grading.internal/", True),      # nit N3: scheme case-insensitivity
    ("http://grading.internal/", True),
    ("http://10.0.0.5:8080/", True),
    ("http://192.168.1.5:8080/", True),      # a private range is still not loopback
])
def test_cleartext_coordinator_transport_warns_only_when_it_leaves_the_box(env, capsys, url, should_warn):
    create_session.warn_if_coordinator_key_travels_in_clear(url)
    warned = "GRADING_API_URL" in capsys.readouterr().err
    assert warned is should_warn


def test_unusable_ca_bundle_is_a_clean_error_not_a_traceback(env, monkeypatch, tmp_path, capsys):
    """Nit N6: the isfile check above it already answers with parser.error; a
    file that exists but isn't PEM was still a naked ssl.SSLError."""
    bogus = tmp_path / "not-a-ca.pem"
    bogus.write_text("this is not a certificate\n")
    exit_code = _run(monkeypatch, _Kasm(), "--ca-bundle", str(bogus))
    assert exit_code is not None and exit_code.code == 2
    assert "--ca-bundle" in capsys.readouterr().err
    assert "Traceback" not in capsys.readouterr().err
