#!/usr/bin/env python3
"""Live fresh/legacy admin checks, called ONLY by the disposable Guacamole CI harness.

No patient data. Requires ipcmc-guac-ci started by test-guacamole-integration.sh.
Tests deny/allow login, existing-volume migration, idempotency and wrong-secret refusal.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "ipcmc-guac-ci",
    "--env-file",
    os.devnull,
    "-f",
    "docker-compose.remote-host.yml",
    "-f",
    "docker-compose.guacamole.yml",
]


def compose(
    *args: str, sql: str | None = None, password: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run bounded disposable-stack command; capture output to avoid secret leaks."""
    env = os.environ.copy()
    if password is not None:
        env["GUACAMOLE_ADMIN_PASSWORD"] = password
    return subprocess.run(
        COMPOSE + list(args),
        input=sql,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def login(password: str, expected_status: int) -> None:
    """Require exact auth status and token shape; errors/redirects are not denials."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    url = "http://127.0.0.1:8090/guacamole/api/tokens"
    data = urllib.parse.urlencode(
        {"username": "guacadmin", "password": password}
    ).encode()
    try:
        with opener.open(url, data=data, timeout=10) as response:
            status = response.status
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, {}
    assert status == expected_status, (
        f"administrator login returned {status}, expected {expected_status}"
    )
    if status == 200:
        token = body.get("authToken")
        assert isinstance(token, str) and token, "login response has no token"
        request = urllib.request.Request(
            url + "/" + urllib.parse.quote(token, safe=""), method="DELETE"
        )
        with opener.open(request, timeout=10) as response:
            assert response.status == 204, "administrator logout failed"


def ready() -> None:
    """Bound restart wait to 90 seconds plus the last request timeout."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8090/guacamole/", timeout=3
            ) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise AssertionError("Guacamole restart timed out")


def main() -> None:
    if os.environ.get("COMPOSE_PROJECT_NAME") != "ipcmc-guac-ci":
        raise SystemExit(
            "Run only through test-guacamole-integration.sh in a disposable checkout"
        )
    # Verify the target database belongs to the disposable project BEFORE any SQL mutation.
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            "ipcmc-guacamole-db",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "ipcmc-guac-ci", "refusing a non-test database"
    containers = compose("ps", "-q")
    assert containers.returncode == 0 and containers.stdout.strip(), (
        "cannot inspect running stack"
    )
    for container in containers.stdout.split():
        ports = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Ports}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        for mappings in json.loads(ports.stdout).values():
            for mapping in mappings or []:
                assert mapping["HostIp"] == "127.0.0.1", (
                    "runtime host binding violates policy"
                )
    password = os.environ["GUACAMOLE_ADMIN_PASSWORD"]
    login("guacadmin", 403)
    login(password, 200)
    assert (
        compose("run", "--rm", "--no-deps", "guacamole-admin-init").returncode == 0
    ), "repeat bootstrap failed"
    assert (
        compose(
            "run",
            "--rm",
            "--no-deps",
            "guacamole-admin-init",
            password="wrong-test-password-" * 3,
        ).returncode
        != 0
    ), "custom credential was overwritten"
    login(password, 200)
    assert compose("stop", "guacamole").returncode == 0, "webapp stop failed"
    # Emulate the pre-upgrade stock account on the SAME database volume.
    sql = """
UPDATE guacamole_user SET
 password_salt=decode('FE24ADC5E11E2B25288D1704ABE67A79E342ECC26064CE69C5B3177795A82264','hex'),
 password_hash=decode('CA458A7D494E3BE824F5E1E175A1556C0F8EEF2C2D7DF3633BEC4A29C4411960','hex'),
 disabled=false
WHERE entity_id=(SELECT entity_id FROM guacamole_entity WHERE name='guacadmin' AND type='USER');
"""
    assert (
        compose(
            "exec",
            "-T",
            "guacamole-db",
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "guacamole",
            "-d",
            "guacamole_db",
            sql=sql,
        ).returncode
        == 0
    ), "legacy fixture setup failed"
    assert (
        compose("run", "--rm", "--no-deps", "guacamole-admin-init").returncode == 0
    ), "existing-volume migration failed"
    assert compose("up", "-d", "guacamole").returncode == 0, "webapp restart failed"
    ready()
    login("guacadmin", 403)
    login(password, 200)
    print("PASS: fresh/legacy admin migration, repeat, mismatch refusal and restart")


if __name__ == "__main__":
    main()
