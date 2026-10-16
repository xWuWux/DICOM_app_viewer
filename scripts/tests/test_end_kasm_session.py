"""scripts/end-kasm-session.py: destroy the kasm, prove it is gone, revoke via the shared library."""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "end-kasm-session.py"
SPEC = importlib.util.spec_from_file_location("end_kasm_session", str(SCRIPT))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

JWT_SENTINEL = "eyJFAKE.jwt.SENTINEL"
KEY_SENTINEL = "kasm-api-key-SENTINEL"
SECRET_SENTINEL = "kasm-api-secret-SENTINEL"


class Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Fake urllib.request.urlopen for the Kasm calls: answers by path, records requests."""

    def __init__(self, destroy_code=200, status_mode="gone"):
        # status_mode: gone (HTTP 404), running, deleted, starting, paused, stopped,
        # no_status (200 without any state), http_403, http_500
        self.requests = []
        self.destroy_code = destroy_code
        self.status_mode = status_mode

    def __call__(self, req, timeout=None, context=None):
        self.requests.append(req)
        url = req.full_url
        if "destroy_kasm" in url:
            if self.destroy_code != 200:
                body = io.BytesIO(json.dumps({"kasm_url": JWT_SENTINEL}).encode())
                raise urllib.error.HTTPError(url, self.destroy_code, "err", {}, body)
            return Resp({"success": True})
        if "get_kasm_status" in url:
            if self.status_mode == "gone":
                raise urllib.error.HTTPError(url, 404, "gone", {}, io.BytesIO(b"{}"))
            if self.status_mode == "paused":
                # The measured paused shape: one key, top-level, no nested kasm
                # object and no kasm_url (issue #159 freeze matrix).
                return Resp({"operational_status": "paused"})
            if self.status_mode == "stopped":
                return Resp({"kasm": {"operational_status": "stopped"}})
            if self.status_mode == "no_status":
                # A 200 that carries no state at either level.
                return Resp({"current_time": 1760000000})
            if self.status_mode in ("http_403", "http_500"):
                code = int(self.status_mode.split("_")[1])
                fp = io.BytesIO(json.dumps({"error_message": JWT_SENTINEL}).encode())
                raise urllib.error.HTTPError(url, code, "err", {}, fp)
            state = {"running": "running", "deleted": "deleted", "starting": "starting"}[self.status_mode]
            return Resp({"kasm": {"operational_status": state}})
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def kasm_env(monkeypatch):
    monkeypatch.setenv("KASM_SERVER", "https://kasm.invalid")
    monkeypatch.setenv("KASM_API_KEY", KEY_SENTINEL)
    monkeypatch.setenv("KASM_API_KEY_SECRET", SECRET_SENTINEL)
    monkeypatch.delenv("GRADING_API_URL", raising=False)
    monkeypatch.delenv("GRADING_COORDINATOR_KEY", raising=False)


def test_happy_path_without_grading_env(kasm_env, capsys):
    opener = Opener()
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    out = capsys.readouterr().out
    assert code == 0
    assert "destroy_kasm: ok" in out
    assert "verify_gone: OK" in out
    assert "revoke_token: skipped" in out


def test_destroy_refused_stops_everything(kasm_env, capsys):
    opener = Opener(destroy_code=500)
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    captured = capsys.readouterr()
    assert code == 1
    assert "HTTP 500" in captured.err
    # the response body of a refused destroy can carry a fresh kasm_url
    assert JWT_SENTINEL not in captured.out + captured.err
    assert len(opener.requests) == 1  # verify/revoke never ran


def test_keys_never_appear_in_any_message(kasm_env, capsys):
    opener = Opener(destroy_code=404)
    mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    captured = capsys.readouterr()
    assert KEY_SENTINEL not in captured.out + captured.err
    assert SECRET_SENTINEL not in captured.out + captured.err


def test_verify_catches_a_still_running_session(kasm_env, capsys):
    opener = Opener(status_mode="running")
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    captured = capsys.readouterr()
    assert code == 1
    assert "still running" in captured.err


def test_starting_after_destroy_counts_as_not_gone(kasm_env, capsys):
    opener = Opener(status_mode="starting")
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    assert code == 1
    assert "starting" in capsys.readouterr().err


def test_paused_session_is_never_reported_as_gone(kasm_env, capsys):
    """The freeze mechanism makes paused the normal resting state, and a paused
    session answers get_kasm_status with a top-level operational_status only. Read
    through the nested shape alone it looked absent, which this script would have
    reported as a successful destroy while the frozen session still sat on disk."""
    opener = Opener(status_mode="paused")
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    captured = capsys.readouterr()
    assert code == 1
    assert "paused" in captured.err
    assert "absent" not in captured.err
    assert JWT_SENTINEL not in captured.out + captured.err


@pytest.mark.parametrize("mode,expect_word", [
    ("http_403", "impersonate"),
    ("http_500", "NOT CONFIRMED"),
    ("no_status", "NOT CONFIRMED"),
    ("stopped", "not destroyed"),
])
def test_nothing_but_real_absence_counts_as_gone(kasm_env, capsys, mode, expect_word):
    """Measured in the lab on 2026-10-16: with a key that may not impersonate the
    session's user, destroy and status BOTH answer 403 "not authorized to impersonate a
    user" while the container is verifiably alive (docker ps -a present, kasms row
    running). Counting any error as "gone or invisible" therefore reported a successful
    end for a destroy that never happened. Only 404 and an explicit deleted state may
    say gone; everything else has to leave the operator with exit 1 and a look."""
    opener = Opener(status_mode=mode)
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    captured = capsys.readouterr()
    assert code == 1, f"{mode} was accepted as a verified end"
    assert expect_word in captured.err
    assert JWT_SENTINEL not in captured.out + captured.err


def test_404_still_counts_as_gone(kasm_env, capsys):
    """The other side of that line, so the fix does not just fail everything."""
    opener = Opener(status_mode="gone")
    assert mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener) == 0


def test_revoke_runs_through_the_shared_library(kasm_env, monkeypatch, capsys):
    monkeypatch.setenv("GRADING_API_URL", "http://grading.invalid/")
    monkeypatch.setenv("GRADING_COORDINATOR_KEY", "coordkey")
    calls = []

    def fake_revoke(student_id, url, key):
        calls.append((student_id, url, key))
        return True

    monkeypatch.setattr(mod, "revoke_for_student", fake_revoke)
    code = mod.main(["--kasm-id", "42", "--user-id", "7", "--student-id", "STU_1"],
                    opener=Opener())
    captured = capsys.readouterr()
    assert code == 0
    assert calls == [("STU_1", "http://grading.invalid/", "coordkey")]
    assert "revoke_token: revoked for STU_1" in captured.out


def test_revoke_failure_is_loud_with_manual_steps(kasm_env, monkeypatch, capsys):
    monkeypatch.setenv("GRADING_API_URL", "http://grading.invalid/")
    monkeypatch.setenv("GRADING_COORDINATOR_KEY", "coordkey")

    def boom(student_id, url, key):
        raise RuntimeError("grading-api unreachable")

    monkeypatch.setattr(mod, "revoke_for_student", boom)
    code = mod.main(["--kasm-id", "42", "--user-id", "7", "--student-id", "STU_1"],
                    opener=Opener())
    captured = capsys.readouterr()
    assert code == 1
    assert 'POST /api/session/revoke with {"student_id": "STU_1"}' in captured.err
    assert "Traceback" not in captured.err


def test_student_id_required_when_revoking(kasm_env, monkeypatch, capsys):
    monkeypatch.setenv("GRADING_API_URL", "http://grading.invalid/")
    monkeypatch.setenv("GRADING_COORDINATOR_KEY", "coordkey")
    opener = Opener()
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=opener)
    assert code == 2
    assert "--student-id" in capsys.readouterr().err
    assert opener.requests == []  # refused before touching Kasm


def test_missing_kasm_config_refused(kasm_env, monkeypatch, capsys):
    monkeypatch.delenv("KASM_SERVER")
    code = mod.main(["--kasm-id", "42", "--user-id", "7"], opener=Opener())
    assert code == 2
    assert "KASM_SERVER" in capsys.readouterr().err
