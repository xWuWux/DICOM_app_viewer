#!/usr/bin/env python3
"""Disposable Docker sentinel check for #216; no live stack or host ports.

Builds the API, verifies file-mode authentication before/after restart, and
checks inspect, running process environments/arguments, and logs. Invoked by
the existing smoke-test CI job. Never prints captured output or secret values.
"""

import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ipcmc/grading-api:coordinator-secret-test"


def docker(*args, check=True, env=None):
    result = subprocess.run(
        ["docker", *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=600,
        env=env,
    )
    if check and result.returncode:
        raise RuntimeError("Docker secret integration step failed (output withheld).")
    return result


PROBE = """
import json, pathlib, urllib.request, urllib.error
key = pathlib.Path('/run/secrets/coordinator').read_text().rstrip('\\n')
request = urllib.request.Request('http://127.0.0.1:8000/session',
    data=json.dumps({'student_id':'SECRET_TEST','session_id':'1'}).encode(),
    headers={'Content-Type':'application/json','X-Coordinator-Key':key})
with urllib.request.urlopen(request, timeout=5) as response:
    token = json.load(response)['token']
request = urllib.request.Request('http://127.0.0.1:8000/case', headers={'X-Grading-Token':token})
with urllib.request.urlopen(request, timeout=5) as response:
    assert json.load(response)['case_id']
request = urllib.request.Request('http://127.0.0.1:8000/session',
    data=b'{"student_id":"DENIED","session_id":"1"}',
    headers={'Content-Type':'application/json','X-Coordinator-Key':'wrong'})
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as exc:
    assert exc.code == 401
else:
    raise AssertionError('wrong key accepted')
"""

PROCESS_CHECK = """
from pathlib import Path
key = Path('/run/secrets/coordinator').read_bytes().rstrip(b'\\n')
checked = 0
for process in Path('/proc').glob('[0-9]*'):
    try:
        for name in ('environ', 'cmdline'):
            assert key not in (process / name).read_bytes(), 'secret in process metadata'
        checked += 1
    except FileNotFoundError:
        pass  # process exited during enumeration
assert checked >= 2, 'must inspect the application as well as this checker'
"""


def main():
    docker("build", "-t", IMAGE, "docker/grading-api")
    container = "coordinator-secret-" + secrets.token_hex(6)
    # Keep the key out of CLI args and env, including setup helpers.
    sentinel = secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix="coordinator-secret-") as directory:
        path = Path(directory) / "key"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(sentinel + "\n")
        compose_env = dict(os.environ)
        compose_env.pop("GRADING_COORDINATOR_KEY", None)
        compose_env.pop("GRADING_COORDINATOR_KEY_FILE", None)
        compose_env.update(
            ORTHANC_PASSWORD="synthetic-compose-only",
            GRADING_COORDINATOR_SECRET_PATH=str(path),
        )
        for base in ("docker-compose.yml", "docker-compose.remote-host.yml"):
            rendered = docker(
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                base,
                "-f",
                "docker-compose.coordinator-secret.yml",
                "config",
                "--format",
                "json",
                env=compose_env,
            ).stdout
            assert sentinel.encode() not in rendered
            settings = json.loads(rendered)["services"]["grading-api"]
            assert not settings["environment"].get("GRADING_COORDINATOR_KEY")
            assert (
                settings["environment"]["GRADING_COORDINATOR_KEY_FILE"]
                == "/run/secrets/grading-coordinator-key"
            )
            assert settings["secrets"][0]["source"] == "grading-coordinator-key"
        docker(
            "run",
            "--rm",
            "--user",
            "0:0",
            "--network",
            "none",
            "--mount",
            f"type=bind,src={directory},dst=/secret-setup",
            "--entrypoint",
            "python3",
            IMAGE,
            "-c",
            "import os; os.chown('/secret-setup/key', 10001, 10001)",
        )
        try:
            docker(
                "run",
                "-d",
                "--name",
                container,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp",
                "--mount",
                f"type=bind,src={path},dst=/run/secrets/coordinator,readonly",
                "-e",
                "GRADING_COORDINATOR_KEY_FILE=/run/secrets/coordinator",
                IMAGE,
            )
            for iteration in range(2):
                for _ in range(30):
                    ready = docker(
                        "exec",
                        container,
                        "python3",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)",
                        check=False,
                    )
                    if ready.returncode == 0:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("File-mode API did not become healthy.")
                docker("exec", container, "python3", "-c", PROBE)
                docker("exec", container, "python3", "-c", PROCESS_CHECK)
                inspected = docker("inspect", container).stdout
                assert sentinel.encode() not in inspected, "secret in Docker metadata"
                config = json.loads(inspected)[0]["Config"]
                assert not any(
                    x.startswith("GRADING_COORDINATOR_KEY=") for x in config["Env"]
                )
                assert sentinel.encode() not in docker("logs", container).stdout
                assert sentinel.encode() not in docker("logs", container).stderr
                if iteration == 0:
                    docker("restart", container)
            print("File-mode authentication, restart and sentinel checks passed.")
        finally:
            docker(
                "rm", "-f", "-v", container
            )  # includes only its anonymous /data volume


if __name__ == "__main__":
    main()
