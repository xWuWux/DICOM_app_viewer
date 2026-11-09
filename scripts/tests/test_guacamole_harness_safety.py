"""The destructive integration harness must refuse existing developer state."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("existing", ["env", "container", "volume", "inventory_error"])
def test_refuses_existing_state_before_build_or_cleanup(tmp_path, existing):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/test-guacamole-integration.sh", scripts)
    env_file = tmp_path / ".env"
    if existing == "env":
        env_file.write_text("DO_NOT_TOUCH=1\n")
    docker = tmp_path / "docker"
    docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
 ps) [ "$EXISTING" != inventory_error ] || exit 1
     [ "$EXISTING" != container ] || echo ipcmc-guacamole
     exit 0;;
 volume) [ "$EXISTING" != volume ] || echo ipcmc-guac-ci_guacamole-db-data
         exit 0;;
 *) echo UNEXPECTED_MUTATION; exit 9;;
esac
""")
    docker.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", str(scripts / "test-guacamole-integration.sh")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CALLS": str(calls),
            "EXISTING": existing,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "UNEXPECTED_MUTATION" not in result.stdout
    if calls.exists():
        assert all(
            line.startswith(("ps ", "volume "))
            for line in calls.read_text().splitlines()
        )
    if existing == "env":
        assert env_file.read_text() == "DO_NOT_TOUCH=1\n"
