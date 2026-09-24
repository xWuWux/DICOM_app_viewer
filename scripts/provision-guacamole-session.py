#!/usr/bin/env python3
"""
Mints a single-use, per-student Guacamole session URL for the DICOM
viewer -- the Guacamole flow's equivalent of scripts/create-session.py's
Kasm flow. See docker-compose.guacamole.yml's own header comment for why
this exists (Kasm Workspaces Community Edition's 5-concurrent-session cap
and non-commercial-use restriction, see CLAUDE.md) and
docker/guacamole-weasis/'s own comments for what's explicitly out of
scope right now (no anti-cheat/hardening -- basic functionality only).

Unlike Kasm's request_kasm API, Guacamole has no built-in per-session
container provisioning and no anonymous quick-link mechanism -- confirmed
via research before building this, not assumed (Guacamole's own
"quickconnect" extension explicitly requires prior authentication; a
community "noauth" fork exists but isn't maintained by Apache and isn't
version-compatibility-guaranteed). This script does both jobs itself:
  1. Mints a grading-api session token (same POST /session mechanism
     create-session.py uses -- identical authorization model regardless
     of which remote-display technology delivers the pixels).
  2. Launches a fresh docker/guacamole-weasis container via `docker run`
     (not docker-compose -- this is a per-student, ephemeral container,
     not a static service, same spirit as Kasm's own per-session
     containers).
  3. Creates a real Guacamole user + VNC connection pointed at that
     container, via Guacamole's REST API (guacamole-auth-jdbc) -- the
     officially-supported way to get a single, no-further-login-prompt
     link per student is a real per-student username/password pair with
     Guacamole's documented URL-parameter auto-login
     (#/?username=...&password=...).
  4. Prints the ready-to-open link.

Requires:
  GRADING_COORDINATOR_KEY  -- same secret create-session.py uses
  GUACAMOLE_URL            -- default http://localhost:8090/guacamole/
  GUACAMOLE_ADMIN_USERNAME -- default guacadmin (Guacamole's own default
                              admin account -- change this for anything
                              beyond a local PoC)
  GUACAMOLE_ADMIN_PASSWORD -- default guacadmin
  GRADING_API_URL          -- default http://localhost:8080/
  DOCKER_NETWORK           -- default dicom_app_viewer_ipcmc-internal
                              (the compose-generated network name;
                              override if your project name differs --
                              check with `docker network ls`)
  GUACAMOLE_WEASIS_IMAGE   -- default ipcmc/guacamole-weasis:poc

Usage:
  GRADING_COORDINATOR_KEY=... python3 scripts/provision-guacamole-session.py --student-id STU_12345

Companion teardown script: scripts/teardown-guacamole-session.py
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def api_call(method, url, data=None, headers=None, fatal=True, label="API"):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        message = f"{label} error calling {url}: {e.code} {e.read().decode()}"
        if fatal:
            sys.exit(message)
        print(f"  (non-fatal) {message}", file=sys.stderr)
        return None


def guac_login(base_url, username, password):
    resp = api_call(
        "POST", f"{base_url}api/tokens",
        data=urllib.parse.urlencode({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        label="Guacamole login",
    )
    return resp["authToken"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--session-id", default=None, help="defaults to a timestamp")
    args = parser.parse_args()

    guac_url = os.environ.get("GUACAMOLE_URL", "http://localhost:8090/guacamole/")
    if not guac_url.endswith("/"):
        guac_url += "/"
    admin_user = os.environ.get("GUACAMOLE_ADMIN_USERNAME", "guacadmin")
    admin_pass = os.environ.get("GUACAMOLE_ADMIN_PASSWORD", "guacadmin")
    coordinator_key = os.environ["GRADING_COORDINATOR_KEY"]
    grading_api_url = os.environ.get("GRADING_API_URL", "http://localhost:8080/")
    docker_network = os.environ.get("DOCKER_NETWORK", "dicom_app_viewer_ipcmc-internal")
    image = os.environ.get("GUACAMOLE_WEASIS_IMAGE", "ipcmc/guacamole-weasis:poc")

    student_id = args.student_id
    session_id = args.session_id or str(int(time.time()))

    # 1. grading-api token -- identical mechanism to create-session.py.
    session_created = api_call(
        "POST", f"{grading_api_url.rstrip('/')}/api/session",
        data={"student_id": student_id, "session_id": session_id},
        headers={"Content-Type": "application/json", "X-Coordinator-Key": coordinator_key},
        label="grading-api",
    )
    grading_token = session_created["token"]

    # 2. Fresh per-student container. Its name doubles as the VNC
    # "hostname" Guacamole connects to -- Docker's embedded DNS resolves
    # container names on a shared network, no need to inspect an IP.
    #
    # issue #53: real VNC auth, not -SecurityTypes None. token_hex(4) (8
    # hex chars) deliberately, not token_urlsafe like guac_password below
    # -- classic VNC/RFB auth only ever uses the first 8 bytes of the
    # password, a protocol limitation, not a mistake to "fix" by
    # generating something longer. Handed to the container via
    # VNC_PASSWORD (docker-entrypoint.sh writes it into TigerVNC's own
    # password file) and to Guacamole's connection config below, as the
    # same secret both ends of that VNC handshake need to agree on.
    vnc_password = secrets.token_hex(4)
    container_name = f"guac-weasis-{student_id}-{session_id}"
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
    run_result = subprocess.run(
        [
            "docker", "run", "-d", "--name", container_name,
            "--network", docker_network,
            # No -p/--publish -- this container's VNC port must only ever
            # be reachable from guacd over docker_network, never a
            # published host port (issue #53; see
            # docker/guacamole-weasis/docker-entrypoint.sh's own comment).
            # See scripts/tests/test_provision_guacamole_session.py for
            # the regression test guarding this.
            #
            # See docker/guacamole-weasis/launch-session.sh's own comment:
            # the grading panel's browser needs unprivileged user
            # namespaces for its own internal sandboxing (bubblewrap) --
            # confirmed the hard way this container's default seccomp
            # profile blocks that outright. A real hardening pass should
            # use a narrower custom seccomp profile, not blanket
            # unconfined -- tracked as a known gap, not an oversight.
            "--security-opt", "seccomp=unconfined",
            "-e", f"STUDENT_ID={student_id}",
            "-e", f"SESSION_ID={session_id}",
            "-e", f"GRADING_TOKEN={grading_token}",
            "-e", f"VNC_PASSWORD={vnc_password}",
            image,
        ],
        capture_output=True, text=True,
    )
    if run_result.returncode != 0:
        sys.exit(f"docker run failed: {run_result.stderr}")

    # Best-effort, not a hard readiness gate (matches create-session.py's
    # own non-fatal-readiness-check philosophy for the Kasm flow) -- gives
    # TigerVNC a moment to actually start listening before Guacamole's
    # first connection attempt.
    time.sleep(3)

    # 3. Guacamole user + connection, via the REST API. See this file's
    # own module docstring for why a real per-student user, not an
    # anonymous quick-link (FOSS Guacamole has no supported equivalent).
    admin_token = guac_login(guac_url, admin_user, admin_pass)

    guac_username = f"stu_{student_id}"
    guac_password = secrets.token_urlsafe(16)

    # Pre-cleanup: a re-run for the same student_id (e.g. minting a fresh
    # link after their previous one expired) would otherwise 400 on a
    # duplicate username. Best-effort, not fatal if there's nothing to
    # clean up yet.
    api_call(
        "DELETE", f"{guac_url}api/session/data/postgresql/users/{guac_username}?token={admin_token}",
        fatal=False, label="Guacamole (pre-cleanup)",
    )
    api_call(
        "POST", f"{guac_url}api/session/data/postgresql/users?token={admin_token}",
        data={"username": guac_username, "password": guac_password, "attributes": {}},
        headers={"Content-Type": "application/json"},
        label="Guacamole (create user)",
    )
    connection = api_call(
        "POST", f"{guac_url}api/session/data/postgresql/connections?token={admin_token}",
        data={
            "parentIdentifier": "ROOT",
            "name": container_name,
            "protocol": "vnc",
            # "password" here is the VNC/RFB auth secret (issue #53), a
            # completely separate credential from guac_password above
            # (that one's Guacamole's own web-login password for this
            # student's account) -- same vnc_password the container's own
            # VNC_PASSWORD env var was given, above.
            "parameters": {"hostname": container_name, "port": "5901", "password": vnc_password},
            "attributes": {},
        },
        headers={"Content-Type": "application/json"},
        label="Guacamole (create connection)",
    )
    connection_id = connection["identifier"]

    api_call(
        "PATCH", f"{guac_url}api/session/data/postgresql/users/{guac_username}/permissions?token={admin_token}",
        data=[{"op": "add", "path": f"/connectionPermissions/{connection_id}", "value": "READ"}],
        headers={"Content-Type": "application/json"},
        label="Guacamole (grant permission)",
    )

    link = f"{guac_url}#/?username={urllib.parse.quote(guac_username)}&password={urllib.parse.quote(guac_password)}"

    print(json.dumps({
        "student_id": student_id,
        "session_id": session_id,
        "container_name": container_name,
        "guacamole_username": guac_username,
        "connection_id": connection_id,
        "link": link,
    }, indent=2))


if __name__ == "__main__":
    main()
