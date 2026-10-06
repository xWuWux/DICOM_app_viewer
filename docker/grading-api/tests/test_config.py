"""
Tests for startup configuration validation (issue #97): the service must
refuse to start with a missing/empty/too-short GRADING_COORDINATOR_KEY
or a nonsensical GRADING_TOKEN_TTL_SECONDS, with a message naming the
variable -- and must never echo the secret's own value into that
message (issue #93's lesson: failure paths leak what they print).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import (
    DEFAULT_TOKEN_TTL_SECONDS,
    MIN_COORDINATOR_KEY_CHARS,
    ConfigError,
    validate_coordinator_key,
    validate_token_ttl_seconds,
)

VALID_KEY = "k" * MIN_COORDINATOR_KEY_CHARS


# ---- the pure validators ----


def test_missing_coordinator_key_is_rejected_with_variable_named():
    with pytest.raises(ConfigError) as excinfo:
        validate_coordinator_key(None)
    assert "GRADING_COORDINATOR_KEY" in str(excinfo.value)


def test_empty_coordinator_key_is_rejected_not_accepted():
    # The exact incident from the audit: "" used to import fine and
    # compare_digest("") meant anyone could mint tokens.
    with pytest.raises(ConfigError):
        validate_coordinator_key("")


def test_short_coordinator_key_is_rejected_without_echoing_value():
    # A distinctive marker, not "s"*len: the message's own prose
    # trivially contains short/common characters ("hex", "short"...), so
    # only a distinctive string proves the value isn't echoed.
    secret_marker = "SECRETVAL-0123456789abcdefgh"
    assert len(secret_marker) < MIN_COORDINATOR_KEY_CHARS
    with pytest.raises(ConfigError) as excinfo:
        validate_coordinator_key(secret_marker)
    message = str(excinfo.value)
    assert "GRADING_COORDINATOR_KEY" in message
    assert secret_marker not in message
    assert str(len(secret_marker)) in message  # the length IS reported


def test_one_char_below_minimum_is_rejected():
    with pytest.raises(ConfigError):
        validate_coordinator_key("k" * (MIN_COORDINATOR_KEY_CHARS - 1))


def test_minimum_length_key_is_accepted_verbatim():
    assert validate_coordinator_key(VALID_KEY) == VALID_KEY


def test_whitespace_only_coordinator_key_is_rejected():
    # CR #111 item 1: 32 spaces passed a bare len() check.
    for blank in [" " * MIN_COORDINATOR_KEY_CHARS, "\t" * 40]:
        with pytest.raises(ConfigError) as excinfo:
            validate_coordinator_key(blank)
        assert "GRADING_COORDINATOR_KEY" in str(excinfo.value)


def test_coordinator_key_with_surrounding_whitespace_is_rejected_not_stripped():
    # Not silently stripped: the client side (create-session.py) sends
    # exact bytes, so server-side normalization would be a silent 401.
    for bad in [VALID_KEY + " ", "\n" + VALID_KEY, VALID_KEY + "\t"]:
        with pytest.raises(ConfigError) as excinfo:
            validate_coordinator_key(bad)
        assert "whitespace" in str(excinfo.value)


def test_ttl_default_used_when_unset():
    assert validate_token_ttl_seconds(None) == DEFAULT_TOKEN_TTL_SECONDS


@pytest.mark.parametrize("bad", ["8 godzin", "", "  ", "28800.5", "0", "-3600", "99999999999"])
def test_bad_ttl_is_rejected_with_variable_named(bad):
    with pytest.raises(ConfigError) as excinfo:
        validate_token_ttl_seconds(bad)
    assert "GRADING_TOKEN_TTL_SECONDS" in str(excinfo.value)


def test_good_ttl_accepted():
    assert validate_token_ttl_seconds("28800") == 28800


def test_ttl_upper_bound_is_enforced():
    from app.config import MAX_TOKEN_TTL_SECONDS

    assert validate_token_ttl_seconds(str(MAX_TOKEN_TTL_SECONDS)) == MAX_TOKEN_TTL_SECONDS
    with pytest.raises(ConfigError) as excinfo:
        validate_token_ttl_seconds(str(MAX_TOKEN_TTL_SECONDS + 1))
    assert "ceiling" in str(excinfo.value)


# ---- the import-time contract, in a real subprocess ----
# (in-process pytest can't re-import app.config per-env -- the whole
# point is what a FRESH interpreter does at import time)


def _run_import(monkeypatch_env):
    import os

    env = {**os.environ, **monkeypatch_env}
    return subprocess.run(
        [sys.executable, "-c", "import app.config"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        timeout=60,
    )


def test_import_exits_cleanly_with_short_key_naming_variable():
    leaky_marker = "k" * (MIN_COORDINATOR_KEY_CHARS - 1)
    proc = _run_import({"GRADING_COORDINATOR_KEY": leaky_marker})
    assert proc.returncode == 2
    assert "GRADING_COORDINATOR_KEY" in proc.stderr
    assert "CONFIGURATION ERROR" in proc.stderr
    assert leaky_marker not in proc.stderr
    # SystemExit, not a traceback: no KeyError/ValueError repr anywhere.
    assert "Traceback" not in proc.stderr


def test_import_exits_cleanly_with_unparseable_ttl():
    proc = _run_import(
        {
            "GRADING_COORDINATOR_KEY": VALID_KEY,
            "GRADING_TOKEN_TTL_SECONDS": "8 godzin",
        }
    )
    assert proc.returncode == 2
    assert "GRADING_TOKEN_TTL_SECONDS" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_import_succeeds_with_valid_config():
    proc = _run_import({"GRADING_COORDINATOR_KEY": VALID_KEY, "GRADING_TOKEN_TTL_SECONDS": "28800"})
    assert proc.returncode == 0, proc.stderr
