"""scripts/reap-guacamole-sessions.py (issue #181) against FAKE Docker and
Guacamole APIs only -- no daemon, no network. SYNTHETIC student ids.
The policy surface is what matters: who gets reaped, who is protected, what
happens on missing signals and half-failed teardowns, and that NOTHING
outside guac-weasis-*/stu_* is ever touched.
"""

import datetime
import importlib.util
import io
import json
import subprocess
import urllib.error
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT_DIR / fname))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gs = _load("gs_under_test", "guacamole_session.py")
reaper_mod = _load("reaper_under_test", "reap-guacamole-sessions.py")


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code=404, body=b"{}"):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


class FakeDocker:
    """Simulates the docker CLI. Honours --filter name= like the real one;
    raw_mode=True simulates a docker version that would NOT filter, to prove
    the library's own startswith guard is the real safety net."""

    def __init__(self, containers=None, started=None, raw_mode=False):
        self.containers = dict(containers or {})
        self.started = dict(started or {})
        self.raw_mode = raw_mode
        self.calls = []

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        if argv[0] == "ps":
            names = sorted(self.containers)
            if not self.raw_mode:
                names = [n for n in names if n.startswith(gs.CONTAINER_PREFIX)]
            rows = [f"{n}|{self.containers[n]}" for n in names]
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="\n".join(rows) + "\n" if rows else "", stderr="")
        if argv[0] == "rm":
            name = argv[-1]
            if name in self.containers:
                del self.containers[name]
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout=name + "\n", stderr="")
            return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="Error response from daemon: No such container: " + name)
        if argv[0] == "inspect":
            name = argv[-1]
            if name in self.started:
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout=self.started[name], stderr="")
            return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="no such")
        raise AssertionError("unexpected docker argv: " + str(argv))


class FakeGuac:
    """Routes the admin API; stateful deletes so idempotency and
    partial-failure retry are real. `active=None` breaks the
    active-connections endpoint (missing-signal path)."""

    def __init__(self, users=None, connections=None, active=None, fail_once=None):
        self.users = dict(users or {})
        self.connections = dict(connections or {})
        self.active = active
        self.fail_once = set(fail_once or ())
        self.calls = []
        self.revoked = []

    def __call__(self, req, timeout=15):
        method, url = req.get_method(), req.full_url
        self.calls.append((method, url.split("?")[0]))
        if url.endswith("api/tokens"):
            return FakeResp(json.dumps({"authToken": "ADMIN-TOKEN"}).encode())
        if "api/v1/session/activeConnections" in url:
            if self.active is None:
                raise http_error(url)
            return FakeResp(json.dumps([{"username": u} for u in self.active]).encode())
        if "postgresql/users" in url and method == "GET":
            return FakeResp(json.dumps({u: {} for u in self.users}).encode())
        if "postgresql/users/" in url and method == "DELETE":
            who = url.split("users/")[1].split("?")[0]
            if "users" in self.fail_once:
                self.fail_once.discard("users")
                raise http_error(url, 500)
            if who not in self.users:
                raise http_error(url, 404)
            self.users.pop(who)
            return FakeResp(b"{}")
        if "postgresql/connections" in url and method == "GET" and "/connections/" not in url:
            return FakeResp(json.dumps({i: {"name": n} for i, n in self.connections.items()}).encode())
        if "/connections/" in url and method == "DELETE":
            cid = url.split("/connections/")[1].split("?")[0]
            if cid not in self.connections:
                raise http_error(url, 404)
            self.connections.pop(cid)
            return FakeResp(b"{}")
        if "api/session/revoke" in url and method == "POST":
            self.revoked.append(json.loads(req.data)["student_id"])
            return FakeResp(json.dumps({"revoked": True}).encode())
        raise AssertionError("unexpected API call: " + method + " " + url)


T0 = 1_760_000_000.0  # fixed synthetic clock


def make_reaper(docker, guac, now_box, logs=None, **over):
    kwargs = dict(
        guac_url="http://fake.local/guacamole/",
        admin_user="admin",
        admin_pass="secret",
        grading_api_url="http://fake.local:8080",
        coordinator_key="K" * 40,
        idle_minutes=180,
        max_age_hours=48,
        state=over.pop("state", None),
    )
    kwargs.update(over)
    logs = logs if logs is not None else []
    r = reaper_mod.Reaper(
        docker,
        guac,
        lambda: now_box[0],
        log=lambda line: logs.append(line),
        **kwargs,
    )
    return r, logs


def session_a(docker_started_offset):
    name = "guac-weasis-STU_A-101"
    docker = FakeDocker(
        containers={name: "running"},
        started={name: iso(T0 + docker_started_offset)},
    )
    return name, docker


def test_overage_session_is_fully_torn_down():
    name, docker = session_a(-49 * 3600)
    guac = FakeGuac(
        users={"stu_STU_A": {}},
        connections={"7": "guac-weasis-STU_A-101"},
        active=set(),
    )
    now = [T0]
    r, logs = make_reaper(docker, guac, now)
    summary = r.run()
    assert name not in docker.containers
    assert guac.users == {}
    assert guac.connections == {}
    assert guac.revoked == ["STU_A"]
    assert summary["reaped"] == 1 and summary["failures"] == 0
    assert r.state == {}


def test_young_connected_session_survives():
    name, docker = session_a(-1 * 3600)
    guac = FakeGuac(
        users={"stu_STU_A": {}},
        connections={"7": name},
        active={"stu_STU_A"},
    )
    now = [T0]
    r, logs = make_reaper(docker, guac, now)
    summary = r.run()
    assert name in docker.containers
    assert summary["reaped"] == 0
    assert r.state[name]["inactive_since"] is None


def test_idle_session_dies_after_the_full_window_across_two_passes():
    name, docker = session_a(-2 * 3600)
    guac = FakeGuac(
        users={"stu_STU_A": {}},
        connections={"7": name},
        active=set(),
    )
    now = [T0]
    r, logs = make_reaper(docker, guac, now)
    r.run()
    assert name in docker.containers  # first inactive pass only STARTS the clock
    assert r.state[name]["inactive_since"] == T0
    now[0] = T0 + 181 * 60
    r.run()
    assert name not in docker.containers
    assert any("no open connection" in l for l in logs)


def test_missing_active_signal_never_idle_reaps():
    name, docker = session_a(-24 * 3600)
    guac = FakeGuac(
        users={"stu_STU_A": {}},
        connections={"7": name},
        active=None,  # endpoint broken -> 404
    )
    now = [T0]
    r, logs = make_reaper(docker, guac, now)
    r.run()
    assert name in docker.containers
    assert any("active-connections endpoint unavailable" in l for l in logs)


def test_foreign_containers_are_never_touched_even_if_docker_filter_lies():
    name_a, docker = session_a(-49 * 3600)
    docker.raw_mode = True
    docker.containers["ipcmc-orthanc"] = "running"
    guac = FakeGuac(users={"stu_STU_A": {}}, active=set())
    r, logs = make_reaper(docker, guac, [T0])
    r.run()
    assert ("rm", "-f", "ipcmc-orthanc") not in docker.calls
    assert "ipcmc-orthanc" in docker.containers
    assert name_a not in docker.containers
    # the foreign row must already be gone at the LIBRARY boundary, so the
    # reaper's own guard is belt-and-braces, never the only line of defence
    listed = gs.docker_list_sessions(docker)
    assert all(c["name"].startswith(gs.CONTAINER_PREFIX) for c in listed)


def test_orphan_user_without_container_is_cleaned_and_token_revoked():
    docker = FakeDocker()
    guac = FakeGuac(
        users={"stu_STU_Z": {}, "guacadmin": {}},
        connections={"9": "guac-weasis-STU_Z-3"},
        active=set(),
    )
    r, logs = make_reaper(docker, guac, [T0])
    summary = r.run()
    assert summary["orphaned_users"] == 1
    assert list(guac.users) == ["guacadmin"]  # admin outside our namespace: invisible
    assert guac.connections == {}
    assert guac.revoked == ["STU_Z"]


def test_orphan_with_open_connection_is_left_for_a_human():
    docker = FakeDocker()
    guac = FakeGuac(users={"stu_STU_Z": {}}, active={"stu_STU_Z"})
    r, logs = make_reaper(docker, guac, [T0])
    r.run()
    assert "stu_STU_Z" in guac.users
    assert any("a human must look" in l for l in logs)


def test_partial_teardown_failure_is_reported_and_completed_next_pass():
    name, docker = session_a(-49 * 3600)
    guac = FakeGuac(
        users={"stu_STU_A": {}},
        connections={"7": name},
        active=set(),
        fail_once={"users"},
    )
    now = [T0]
    r, logs = make_reaper(docker, guac, now)
    summary = r.run()
    assert summary["failures"] == 1 and name not in docker.containers
    assert r.state.get(name)  # kept for retry
    r.run()  # second pass: container gone -> orphan pass finishes the job
    assert guac.users == {}
    assert name not in r.state  # stale state entry dropped


def test_dry_run_deletes_nothing():
    name, docker = session_a(-49 * 3600)
    guac = FakeGuac(users={"stu_STU_A": {}}, connections={"7": name}, active=set())
    r, logs = make_reaper(docker, guac, [T0])
    summary = r.run(dry_run=True)
    assert name in docker.containers and "stu_STU_A" in guac.users
    assert summary["reaped"] == 0
    assert any("dry-run: would reap" in l for l in logs)


def test_library_teardown_is_idempotent_second_run_reports_not_raises():
    name, docker = session_a(0)
    guac = FakeGuac(users={"stu_STU_A": {}}, connections={"7": name}, active=set())
    common = dict(
        opener=guac,
        docker=docker,
        guac_url="http://fake.local/guacamole/",
        admin_user="a",
        admin_pass="b",
    )
    first = gs.teardown_session(name, **common)
    assert all(v == gs.STEP_OK for v in first.values()) or first.get("grading_token", "").startswith("skipped")
    second = gs.teardown_session(name, **common)
    # container already rm'd and user already gone: steps must pass silently
    assert second["container"] == gs.STEP_OK
