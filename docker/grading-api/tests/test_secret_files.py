"""File mode must fail closed without exposing values or changing os.environ."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.secret_files import MAX_SECRET_BYTES, SecretFileError, read_secret

NAME = "GRADING_COORDINATOR_KEY"
SENTINEL = "file-only-sentinel-" + "Q" * 40


@pytest.fixture
def secret(tmp_path):
    path = tmp_path / "key"
    path.write_text(SENTINEL)
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_file_read_does_not_export_value(secret, ending, monkeypatch):
    secret.write_text(SENTINEL + ending)
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.setenv(NAME + "_FILE", str(secret))
    before = dict(os.environ)
    assert read_secret(NAME) == SENTINEL
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    "contents", [b"", b" \n", b"abc\x00def", b"abc\ndef", b"abc\n\n", b"\xff", b"x" * (MAX_SECRET_BYTES + 1)]
)
def test_malformed_file_rejected(secret, contents):
    secret.write_bytes(contents)
    with pytest.raises(SecretFileError):
        read_secret(NAME, {NAME + "_FILE": str(secret)})


def test_missing_file_never_falls_back(secret):
    with pytest.raises(SecretFileError) as exc:
        read_secret(NAME, {NAME: SENTINEL, NAME + "_FILE": str(secret.parent / SENTINEL)})
    assert SENTINEL not in str(exc.value)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666, 0o700])
def test_public_or_executable_file_refused(secret, mode):
    secret.chmod(mode)
    with pytest.raises(SecretFileError):
        read_secret(NAME, {NAME + "_FILE": str(secret)})


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "empty-path", "nul-path"])
def test_bad_path_fails_without_value_or_path_in_error(secret, kind):
    path = secret.parent / SENTINEL
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    configured = "" if kind == "empty-path" else str(path)
    if kind == "nul-path":
        configured += "\0"
    with pytest.raises(SecretFileError) as exc:
        read_secret(NAME, {NAME + "_FILE": configured})
    assert SENTINEL not in str(exc.value)


def test_unreadable_file_is_sanitized(secret, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(SENTINEL)

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(SecretFileError) as exc:
        read_secret(NAME, {NAME + "_FILE": str(secret)})
    assert SENTINEL not in str(exc.value)


def test_legacy_env_remains_byte_exact():
    assert read_secret(NAME, {NAME: SENTINEL}) == SENTINEL
    assert read_secret(NAME, {}) is None


@pytest.mark.parametrize("valid", [True, False])
def test_config_import_file_mode_in_fresh_process(secret, valid):
    if not valid:
        secret.write_text("short-sentinel")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GRADING_")}
    env[NAME + "_FILE"] = str(secret)
    result = subprocess.run(
        [sys.executable, "-c", "from app import config; import os; assert 'GRADING_COORDINATOR_KEY' not in os.environ"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == (0 if valid else 2)
    assert SENTINEL not in result.stdout + result.stderr
    assert "short-sentinel" not in result.stdout + result.stderr
