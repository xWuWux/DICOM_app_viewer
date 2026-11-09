"""Execute the real bootstrap shell against fake psql; runtime SQL/login in CI."""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docker/guacamole/bootstrap-admin.sh"
SALT = "FE24ADC5E11E2B25288D1704ABE67A79E342ECC26064CE69C5B3177795A82264"
HASH = "CA458A7D494E3BE824F5E1E175A1556C0F8EEF2C2D7DF3633BEC4A29C4411960"
PASSWORD = "test-only-admin-with-'quotes-$-and-spaces-123"


@pytest.fixture
def run_bootstrap(tmp_path):
    fake = tmp_path / "psql"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
sql = sys.stdin.read()
p = Path(os.environ['CALLS'])
with p.open('a') as f:
    f.write(json.dumps({'sql': sql, 'args': sys.argv[1:], 'has_password': 'GUACAMOLE_ADMIN_PASSWORD' in os.environ}) + '\\n')
if sql.startswith('SELECT'):
    print(os.environ['ROW'])
    sys.exit(int(os.environ.get('READ_FAIL', '0')))
print(os.environ.get('CHANGED', '1'))
sys.exit(int(os.environ.get('WRITE_FAIL', '0')))
""")
    fake.chmod(0o755)
    calls = tmp_path / "calls"

    def run(password=PASSWORD, row=f"{SALT}|{HASH}|true", **extras):
        env = {
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CALLS": str(calls),
            "ROW": row,
            **extras,
        }
        env.pop("GUACAMOLE_ADMIN_PASSWORD", None)
        if password is not None:
            env["GUACAMOLE_ADMIN_PASSWORD"] = password
        calls.unlink(missing_ok=True)
        result = subprocess.run(
            ["sh", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        recorded = (
            [json.loads(line) for line in calls.read_text().splitlines()]
            if calls.exists()
            else []
        )
        return result, recorded

    return run


@pytest.mark.parametrize("disabled", ["true", "false"])
def test_fresh_disabled_and_existing_enabled_stock_are_migrated(
    run_bootstrap, disabled
):
    result, calls = run_bootstrap(row=f"{SALT}|{HASH}|{disabled}")
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    sql = calls[1]["sql"]
    salt = re.search(r"SET password_salt=decode\('([A-F0-9]+)'", sql)[1]
    digest = re.search(r"password_hash=decode\('([a-f0-9]+)'", sql)[1]
    assert len(salt) == 64 and salt != SALT
    assert hashlib.sha256((PASSWORD + salt).encode()).hexdigest() == digest
    assert "disabled=false" in sql and "SELECT count(*) FROM changed" in sql
    assert f"u.password_hash=decode('{HASH}'" in sql
    assert PASSWORD not in str(calls) + result.stdout + result.stderr
    assert all(not call["has_password"] for call in calls)


def test_matches_official_guacamole_password_vector():
    assert hashlib.sha256(("guacadmin" + SALT).encode()).hexdigest().upper() == HASH


def test_matching_custom_password_is_not_rotated(run_bootstrap):
    digest = hashlib.sha256((PASSWORD + SALT).encode()).hexdigest().upper()
    result, calls = run_bootstrap(row=f"{SALT}|{digest}|false")
    assert result.returncode == 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    "row",
    [
        "",
        "NULL|NULL|false",
        f"{SALT}|{'A' * 64}|false",
        f"{SALT}|{'A' * 64}|true",
        "malformed",
    ],
)
def test_missing_mismatched_or_disabled_custom_account_fails_without_update(
    run_bootstrap, row
):
    result, calls = run_bootstrap(row=row)
    assert result.returncode != 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    "password",
    [
        None,
        "",
        "guacadmin",
        "short",
        "x" * 1025,
        " " * 32,
        "x" * 32 + "\n",
        " " + "x" * 32,
    ],
)
def test_invalid_password_fails_before_database(run_bootstrap, password):
    result, calls = run_bootstrap(password=password)
    assert result.returncode != 0
    assert calls == []


@pytest.mark.parametrize(
    "extras", [{"READ_FAIL": "1"}, {"WRITE_FAIL": "1"}, {"CHANGED": "0"}]
)
def test_database_and_concurrent_change_failures_do_not_report_success(
    run_bootstrap, extras
):
    result, _ = run_bootstrap(**extras)
    assert result.returncode != 0
    assert "migrated" not in result.stdout
    assert PASSWORD not in result.stderr


def test_cli_secret_reader_rejects_defaults_without_echo(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "gs_admin", ROOT / "scripts/guacamole_session.py"
    )
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    for value in ("", " ", "guacadmin"):
        monkeypatch.setenv("GUACAMOLE_ADMIN_PASSWORD", value)
        with pytest.raises(ValueError):
            gs.admin_password()
    monkeypatch.setenv("GUACAMOLE_ADMIN_PASSWORD", PASSWORD)
    assert gs.admin_password() == PASSWORD


@pytest.mark.parametrize(
    "script,args",
    [
        ("provision-guacamole-session.py", ["--student-id", "STU_TEST"]),
        (
            "teardown-guacamole-session.py",
            ["--student-id", "STU_TEST", "--session-id", "123"],
        ),
        ("reap-guacamole-sessions.py", ["--dry-run"]),
    ],
)
@pytest.mark.parametrize("value", [None, "guacadmin"])
def test_session_clis_refuse_missing_or_default_password_before_effects(
    script, args, value, tmp_path
):
    import sys

    env = os.environ.copy()
    env.pop("GUACAMOLE_ADMIN_PASSWORD", None)
    if value is not None:
        env["GUACAMOLE_ADMIN_PASSWORD"] = value
    env["GUACAMOLE_LINK_DIR"] = str(tmp_path / "links")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "GUACAMOLE_ADMIN_PASSWORD" in result.stderr
    assert not (tmp_path / "links").exists()
