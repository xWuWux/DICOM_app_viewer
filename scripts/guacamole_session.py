#!/usr/bin/env python3
"""Shared Guacamole-session destruction primitives (issue #181, part of #177).

Single source of truth for "what belongs to a session" and how to remove it,
extracted from scripts/teardown-guacamole-session.py so the manual CLI and
the automatic reaper (scripts/reap-guacamole-sessions.py) can never drift
apart. Guacamole itself has no destroy-on-logout and no idle detection
(see docker-compose.guacamole.yml's header comment); ephemerality is OUR
responsibility (CLAUDE.md: containers destroyed, zero persistence).

Namespace guard (#181 acceptance criteria: "never deletes anything outside
the guac- namespace"): this module only ever accepts names that
scripts/provision-guacamole-session.py itself creates --

    container name        guac-weasis-<student_id>-<session_id>
    guacamole user        stu_<student_id>
    guacamole connection  same name as the container

-- and REFUSES anything else BEFORE its first network or docker call.

Every entry point takes its transport explicitly (``opener`` for HTTP,
``docker`` for the docker CLI) so the pytest suites run the full logic
against fakes; defaults are the real urllib/subprocess transports.
Independent step failures are RETURNED as per-step results, never hidden
behind an early sys.exit (#182's lesson: half-cleaned state must be visible
and retryable).
"""

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

CONTAINER_PREFIX = "guac-weasis-"
GUAC_USER_PREFIX = "stu_"

# Shapes mirror the provisioner's own CLI contract: student ids are
# pseudonyms like STU_12345, session ids are the numeric part of a link.
CONTAINER_NAME_RE = re.compile(
    r"^guac-weasis-([A-Za-z0-9][A-Za-z0-9_.-]{0,63})-(\d{1,20})$"
)
GUAC_USER_RE = re.compile(r"^stu_([A-Za-z0-9][A-Za-z0-9_.-]{0,63})$")

STEP_OK = "ok"  # per-step report marker; anything else in a report is an error string


class NamespaceViolation(Exception):
    """Raised BEFORE any side effect when a name is outside the guac- namespace."""


class APIError(Exception):
    def __init__(self, code, url, excerpt=""):
        Exception.__init__(self, f"HTTP {code} from {url}: {excerpt}")
        self.code = code
        self.url = url
        self.excerpt = excerpt


def parse_container_name(name):
    """-> (student_id, session_id). Raises NamespaceViolation otherwise."""
    m = CONTAINER_NAME_RE.match(name or "")
    if not m:
        raise NamespaceViolation(
            f"refusing to touch '{name}': not a '{CONTAINER_PREFIX}<student>-<session>' container"
        )
    return m.group(1), m.group(2)


def parse_guac_username(username):
    """-> student_id. Raises NamespaceViolation otherwise."""
    m = GUAC_USER_RE.match(username or "")
    if not m:
        raise NamespaceViolation(
            f"refusing to touch '{username}': not a '{GUAC_USER_PREFIX}<student>' guacamole user"
        )
    return m.group(1)


def http_json(opener, method, url, data=None, headers=None, timeout=15):
    """One HTTP call -> parsed JSON ({} for empty bodies). Raises APIError on
    HTTP failure with the body excerpt truncated to 200 chars: Guacamole
    echoes request objects back, and connection attributes carry the VNC
    password -- raw bodies must not end up in reaper logs."""
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        # The admin token rides in ?token=<...> on EVERY guacamole URL; a
        # leaked APIError string would leak a live credential into cron
        # mail / CI logs. Scrub at the ONE chokepoint that builds them.
        safe_url = re.sub(r"([?&])token=[^&]*", r"\1token=<redacted>", url)
        raise APIError(
            exc.code, safe_url, exc.read().decode(errors="replace")[:200]
        ) from None


def real_opener(request, timeout=15):
    return urllib.request.urlopen(request, timeout=timeout)


def real_docker(argv):
    return subprocess.run(
        ["docker"] + argv, capture_output=True, text=True, check=False
    )


# --------------------------------------------------------------------------
# Guacamole REST (base_url ends with '/', e.g. http://localhost:8090/guacamole/)
# --------------------------------------------------------------------------


def guac_login(opener, base_url, admin_user, admin_pass):
    resp = http_json(
        opener,
        "POST",
        base_url + "api/tokens",
        data=urllib.parse.urlencode(
            {"username": admin_user, "password": admin_pass}
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return resp["authToken"]


def guac_list_users(opener, base_url, token):
    data = http_json(
        opener, "GET", f"{base_url}api/session/data/postgresql/users?token={token}"
    )
    return list((data or {}).keys())


def guac_delete_user(opener, base_url, token, username):
    """404 (already gone) counts as success: teardown must be idempotent."""
    parse_guac_username(username)
    try:
        http_json(
            opener,
            "DELETE",
            f"{base_url}api/session/data/postgresql/users/{username}?token={token}",
        )
    except APIError as exc:
        if exc.code != 404:
            raise
    return True


def guac_list_connections(opener, base_url, token):
    data = http_json(
        opener,
        "GET",
        f"{base_url}api/session/data/postgresql/connections?token={token}",
    )
    return data or {}


def guac_delete_connection_by_name(opener, base_url, token, name):
    """Delete every connection object named exactly `name` (the provisioner
    names connections after the container; deleting a user does NOT delete
    its connections -- see teardown-guacamole-session.py's original comment).
    -> number deleted."""
    if not (name or "").startswith(CONTAINER_PREFIX):
        raise NamespaceViolation(
            f"refusing to delete connection '{name}': not a '{CONTAINER_PREFIX}...' connection"
        )
    deleted = 0
    for conn_id, conn in guac_list_connections(opener, base_url, token).items():
        if conn.get("name") == name:
            http_json(
                opener,
                "DELETE",
                f"{base_url}api/session/data/postgresql/connections/{conn_id}?token={token}",
            )
            deleted += 1
    return deleted


def guac_active_usernames(opener, base_url, token):
    """Usernames with a currently OPEN connection (core REST v1, Guacamole
    1.6). Raises APIError on any problem -- CALLERS must treat "unknown" as
    ACTIVE (never kill a session on a missing signal)."""
    data = http_json(
        opener, "GET", f"{base_url}api/v1/session/activeConnections?token={token}"
    )
    return {entry.get("username") for entry in data or [] if entry.get("username")}


# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------


def docker_rm(docker, container_name):
    """docker rm -f; 'no such container' counts as success (idempotent)."""
    parse_container_name(container_name)
    proc = docker(["rm", "-f", container_name])
    if proc.returncode == 0:
        return True
    if "no such container" in (proc.stderr or "").lower():
        return True
    raise RuntimeError(
        f"docker rm -f {container_name} failed: {(proc.stderr or '').strip()[:200]}"
    )


def docker_list_sessions(docker):
    """-> list of dicts {name, state} for every guac-weasis- container
    (running OR exited -- an exited one still holds the name and the
    Guacamole objects)."""
    proc = docker(
        [
            "ps",
            "-a",
            "--filter",
            f"name={CONTAINER_PREFIX}",
            "--format",
            "{{.Names}}|{{.State}}",
        ]
    )
    containers = []
    for line in (proc.stdout or "").splitlines():
        name, _, state = line.partition("|")
        if name.startswith(
            CONTAINER_PREFIX
        ):  # the guard again: a filter mismatch must never slip through
            containers.append({"name": name, "state": state.strip()})
    return containers


def docker_started_at(docker, container_name):
    """-> unix timestamp (float) or None when unparseable."""
    proc = docker(["inspect", "-f", "{{.State.StartedAt}}", container_name])
    raw = (proc.stdout or "").strip()
    if not raw or raw.startswith("0001-01-01"):  # docker's zero timestamp
        return None
    import datetime

    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# grading-api token revocation (issue #104 endpoint)
# --------------------------------------------------------------------------


def revoke_token_for_student(opener, grading_api_url, coordinator_key, student_id):
    """-> True revoked, False nothing-to-revoke. Raises APIError/URLError on
    connectivity problems so the reaper can distinguish 'retry later' from
    'done'. student_id comes from a PARSED container/user name, never raw."""
    body = {"student_id": student_id}
    resp = http_json(
        opener,
        "POST",
        grading_api_url.rstrip("/") + "/api/session/revoke",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Coordinator-Key": coordinator_key,
        },
    )
    return bool(resp.get("revoked"))


# --------------------------------------------------------------------------
# Composite teardown -- the extracted teardown-guacamole-session.py logic
# --------------------------------------------------------------------------


def teardown_session(
    container_name,
    *,
    opener=real_opener,
    docker=real_docker,
    guac_url,
    admin_user,
    admin_pass,
    grading_api_url=None,
    coordinator_key=None,
    verbose=False,
):
    """Destroy EVERYTHING belonging to one session: container, guacamole
    user + connection, grading-api token. Container/student/session all
    derived from `container_name` (one trusted shape, no mixed inputs).

    -> dict of step-name -> "ok" or an error string. Steps are independent:
    a guacamole 500 must not leave the container running. The CLI exits
    non-zero when any step reports a failure; the reaper retries on its
    next pass because nothing was forgotten.
    """
    if not guac_url.endswith("/"):
        guac_url += "/"
    student_id, _session_id = parse_container_name(container_name)
    guac_username = GUAC_USER_PREFIX + student_id
    report = {}

    def step(name, fn):
        try:
            fn()
            report[name] = STEP_OK
        except Exception as exc:  # noqa: BLE001 - every step reports, none aborts
            report[name] = f"{type(exc).__name__}: {exc}"
        if verbose:
            print(f"  [{report[name] if report[name] == STEP_OK else 'FAILED'}] {name}")

    step("container", lambda: docker_rm(docker, container_name))

    def guac_part():
        token = guac_login(opener, guac_url, admin_user, admin_pass)
        guac_delete_user(opener, guac_url, token, guac_username)
        guac_delete_connection_by_name(opener, guac_url, token, container_name)

    step("guacamole", guac_part)

    def revoke_part():
        if not coordinator_key:
            # A reaper run without the coordinator key CANNOT revoke; the
            # operator must see that explicitly, and the token's own TTL
            # remains the only expiry. Report, never crash.
            raise RuntimeError(
                "GRADING_COORDINATOR_KEY not configured: token left to expire on its own"
            )
        revoke_token_for_student(opener, grading_api_url, coordinator_key, student_id)

    if grading_api_url:
        step("grading_token", revoke_part)
    else:
        report["grading_token"] = "skipped (no GRADING_API_URL)"

    return report
