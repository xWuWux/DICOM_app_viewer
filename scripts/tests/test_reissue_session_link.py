"""scripts/reissue-session-link.py: fresh link to stdout, and the JWT class of leak guarded."""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "reissue-session-link.py"
SPEC = importlib.util.spec_from_file_location("reissue_session_link", str(SCRIPT))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

JWT = "eyJFAKE.jwt.SENTINEL"
LINK = f"https://kasm.invalid/#/connect/kasm/42/7/{JWT}"


class Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_mode(mode):
    """Fake urlopen keyed by scenario; the request is never echoed by the script."""

    def opener(req, timeout=None, context=None):
        if mode == "running":
            return Resp({"kasm": {"kasm_url": f"/#/connect/kasm/42/7/{JWT}",
                                  "operational_status": "running"}})
        if mode == "starting":
            return Resp({"kasm": {"kasm_url": f"/#/connect/kasm/42/7/{JWT}",
                                  "operational_status": "starting"}})
        if mode == "toplevel":
            return Resp({"kasm_url": f"/#/connect/kasm/42/7/{JWT}",
                         "kasm": {"operational_status": "running"}})
        if mode == "stopped":
            return Resp({"kasm": {"kasm_url": f"/#/connect/kasm/42/7/{JWT}",
                                  "operational_status": "stopped"}})
        if mode == "no_url":
            return Resp({"kasm": {"operational_status": "running"}})
        if mode == "paused":
            # Exactly the shape measured on Kasm 1.19.0 CE for a paused session:
            # one key, top-level, no kasm_url anywhere in it.
            return Resp({"operational_status": "paused"})
        if mode == "measured_running":
            # The exact shape from the #159 matrix: top-level kasm_url, state only
            # inside the nested kasm object, plus the current_time field Kasm sends.
            return Resp({"current_time": 1760000000,
                         "kasm_url": f"/#/connect/kasm/42/7/{JWT}",
                         "kasm": {"operational_status": "running"}})
        if mode == "http_403":
            # Measured answer for a key that may not impersonate the session's user.
            # The body carries a JWT here on purpose, exactly as an error body can on
            # this API, to prove the cause is named without echoing what came back.
            raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {},
                                         io.BytesIO(json.dumps({"error_message": JWT}).encode()))
        if mode == "err_with_jwt":
            fp = io.BytesIO(json.dumps({"kasm_url": JWT, "detail": "gone"}).encode())
            raise urllib.error.HTTPError(req.full_url, 500, "err", {}, fp)
        raise AssertionError(f"unknown mode {mode}")

    return opener


@pytest.fixture
def kasm_env(monkeypatch):
    monkeypatch.setenv("KASM_SERVER", "https://kasm.invalid")
    monkeypatch.setenv("KASM_API_KEY", "k")
    monkeypatch.setenv("KASM_API_KEY_SECRET", "s")


def test_prints_fresh_link_on_stdout(kasm_env, capsys):
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("running"))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == LINK + "\n"  # stdout carries the link and nothing else
    assert captured.err == ""


def test_starting_state_is_fine(kasm_env, capsys):
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("starting"))
    assert code == 0
    assert LINK in capsys.readouterr().out


def test_top_level_kasm_url_accepted(kasm_env, capsys):
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("toplevel"))
    assert code == 0
    assert LINK in capsys.readouterr().out


def test_stopped_state_warns_but_prints(kasm_env, capsys):
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("stopped"))
    captured = capsys.readouterr()
    assert code == 0
    assert "stopped" in captured.err  # the state only -- never the JWT
    assert JWT not in captured.err
    assert LINK in captured.out


def test_answer_without_kasm_url_fails_cleanly(kasm_env, capsys):
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("no_url"))
    captured = capsys.readouterr()
    assert code == 1
    assert "kasm_url" in captured.err
    assert JWT not in captured.out + captured.err


def test_measured_running_shape_prints_without_warning(kasm_env, capsys):
    """The exact shape every running/resumed public get_kasm_status in the #159 matrix
    had: top_keys ['current_time','kasm','kasm_url'] -- link at the top level, state
    only inside the nested kasm object. A shape lock rather than a fix check: this
    answer was already handled before the paused fix, and pinning it stops a future
    reader from taking the paused shape for the general one."""
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("measured_running"))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == LINK + "\n"
    assert captured.err == ""


def test_paused_session_is_told_to_resume_not_remint(kasm_env, capsys):
    """A paused session carries no link, so this fails -- but 'the session may be
    gone' would send an operator to destroy a session frozen on purpose."""
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("paused"))
    captured = capsys.readouterr()
    assert code == 3  # 3 = paused and resumable, 1 = nothing to re-link
    assert captured.out == ""
    assert "PAUSED" in captured.err
    assert "Resume it first" in captured.err
    assert "may be gone" not in captured.err
    assert "42" in captured.err  # names the session it is talking about
    assert JWT not in captured.out + captured.err


def test_http_403_names_the_impersonate_permission_without_echoing_the_body(kasm_env, capsys):
    """Measured: this endpoint answers 403 "not authorized to impersonate a user" to a
    key without that permission (granting 50 and 100 together was still not enough).
    Shown only "HTTP 403", an operator hunts for a dead session instead of fixing the
    key -- and the body still may never be echoed, since an error body can carry a
    fresh kasm_url like any other answer on this API."""
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("http_403"))
    captured = capsys.readouterr()
    assert code == 1
    assert "HTTP 403" in captured.err
    assert "impersonate" in captured.err
    assert JWT not in captured.out + captured.err


def test_status_is_called_once_never_polled(kasm_env, capsys):
    """Inertia guard rather than a fix check: this script already asked exactly once
    before the paused fix, and this test exists so it never grows a retry loop. Each
    get_kasm_status call mints a new session_token row and a fresh JWT (the #159 matrix
    saw three different links from three calls on one session), so a poll here would
    mint one live link per attempt."""
    calls = []

    def counting_opener(req, timeout=None, context=None):
        calls.append(req.full_url)
        return Resp({"kasm_url": f"/#/connect/kasm/42/7/{JWT}", "operational_status": "running"})

    assert mod.main(["--kasm-id", "42", "--user-id", "7"], opener=counting_opener) == 0
    assert calls == ["https://kasm.invalid/api/public/get_kasm_status"]


def test_error_body_carrying_jwt_is_never_logged(kasm_env, capsys):
    """Acceptance criterion (#159): a response body that looks like an error can
    still carry a fresh kasm_url; nothing may reach stdout/stderr but the type."""
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("err_with_jwt"))
    captured = capsys.readouterr()
    assert code == 1
    assert JWT not in captured.out
    assert JWT not in captured.err
    assert "HTTP 500" in captured.err  # diagnosable without echoing the body


def test_missing_env_refused(kasm_env, monkeypatch, capsys):
    monkeypatch.delenv("KASM_API_KEY_SECRET")
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener_mode("running"))
    assert code == 2
    assert "KASM_API_KEY_SECRET" in capsys.readouterr().err
