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
