#!/usr/bin/env python3
"""
Tears down a Guacamole PoC session minted by
scripts/provision-guacamole-session.py: stops+removes the per-student
container, and deletes the Guacamole user + connection it created.

Unlike Kasm's own agent, Guacamole/guacd have no automatic idle-session
detection or container lifecycle management -- that's Kasm-agent-
equivalent functionality explicitly out of scope for this basic-
functionality PoC (see docker-compose.guacamole.yml's own header
comment). Until a real automatic-teardown mechanism exists, a real
deployment needs to run this explicitly (or on a schedule) to avoid
accumulating orphaned containers/Guacamole users session after session.

Usage:
  python3 scripts/teardown-guacamole-session.py --student-id STU_12345 --session-id 1234567890
"""
import argparse
import json
import os
import subprocess
import sys
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()

    guac_url = os.environ.get("GUACAMOLE_URL", "http://localhost:8090/guacamole/")
    if not guac_url.endswith("/"):
        guac_url += "/"
    admin_user = os.environ.get("GUACAMOLE_ADMIN_USERNAME", "guacadmin")
    admin_pass = os.environ.get("GUACAMOLE_ADMIN_PASSWORD", "guacadmin")

    container_name = f"guac-weasis-{args.student_id}-{args.session_id}"
    guac_username = f"stu_{args.student_id}"

    print(f"Stopping and removing container {container_name}...")
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)

    token_resp = api_call(
        "POST", f"{guac_url}api/tokens",
        data=urllib.parse.urlencode({"username": admin_user, "password": admin_pass}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        label="Guacamole login",
    )
    admin_token = token_resp["authToken"]

    print(f"Deleting Guacamole user {guac_username}...")
    api_call(
        "DELETE", f"{guac_url}api/session/data/postgresql/users/{guac_username}?token={admin_token}",
        fatal=False, label="Guacamole (delete user)",
    )

    # The connection is named after the container, and deleting the user
    # above does NOT delete the connection object itself (connections are
    # independent objects, just no longer reachable by anyone once the
    # only user with permission on it is gone) -- look it up by name and
    # remove it too, so it doesn't linger in the admin UI indefinitely.
    connections = api_call(
        "GET", f"{guac_url}api/session/data/postgresql/connections?token={admin_token}",
        fatal=False, label="Guacamole (list connections)",
    )
    if connections:
        for conn_id, conn in connections.items():
            if conn.get("name") == container_name:
                print(f"Deleting Guacamole connection {conn_id} ({container_name})...")
                api_call(
                    "DELETE",
                    f"{guac_url}api/session/data/postgresql/connections/{conn_id}?token={admin_token}",
                    fatal=False, label="Guacamole (delete connection)",
                )

    print("Done.")


if __name__ == "__main__":
    main()
