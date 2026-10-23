"""Real uvicorn, real SQLite, deterministic crash points, synthetic data only."""

import concurrent.futures
import importlib.util
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
KEY = "shutdown-test-only-coordinator-key-0123456789"
DOCKER_CMD = json.loads(
    next(
        line[4:]
        for line in (ROOT / "docker/grading-api/Dockerfile").read_text().splitlines()
        if line.startswith("CMD ")
    )
)
DRAIN_SECONDS = DOCKER_CMD[DOCKER_CMD.index("--timeout-graceful-shutdown") + 1]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached before deadline")


class Server:
    def __init__(self, directory, **env):
        directory.mkdir(exist_ok=True)
        self.path = directory / "grading.db"
        self.marker = directory / "entered"
        self.marker.unlink(missing_ok=True)
        self.marker.with_suffix(".release").unlink(missing_ok=True)
        self.log_path = directory / f"server-{time.time_ns()}.log"
        self.log = self.log_path.open("w")
        # Keep the bound socket until inherited by uvicorn; no free-port race.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests_shutdown_app:app",
                "--fd",
                str(sock.fileno()),
                "--no-access-log",
                "--timeout-graceful-shutdown",
                DRAIN_SECONDS,
            ],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [str(ROOT / "docker/grading-api"), str(Path(__file__).parent)]
                ),
                "GRADING_DB_PATH": str(self.path),
                "GRADING_COORDINATOR_KEY": KEY,
                "GRADING_ORTHANC_URL": "",
                "GRADING_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS": "0",
                "GRADING_HEALTH_CACHE_SECONDS": "0",
                "GRADING_CASES_FILE": "",
                "TEST_MARKER": str(self.marker),
                **env,
            },
            pass_fds=(sock.fileno(),),
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        sock.close()
        self.client = httpx.Client(base_url=self.url, timeout=12, trust_env=False)

    def start(self):
        def alive():
            assert self.proc.poll() is None, self.logs()
            try:
                return self.client.get("/livez").status_code == 200
            except httpx.TransportError:
                return False

        wait_for(alive, timeout=30)
        return self

    def logs(self):
        return self.log_path.read_text()

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)
        self.client.close()
        self.log.close()

    def prepare(self, student):
        r = self.client.post(
            "/session",
            json={"student_id": student, "session_id": "test"},
            headers={"X-Coordinator-Key": KEY},
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        r = self.client.get("/case", headers={"X-Grading-Token": token})
        assert r.status_code == 200, r.text
        return {
            "token": token,
            "case_id": r.json()["case_id"],
            "stage": "learning",
            "text": "synthetic",
        }


@pytest.fixture
def servers():
    active = []

    def make(path, **env):
        server = Server(path, **env)
        active.append(server)
        return server

    yield make
    for server in reversed(active):
        server.close()


def assert_answer(path, student, committed):
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT count(*) FROM submissions WHERE student_id=?", (student,)
        ).fetchone()[0] == int(committed)
        stage = conn.execute(
            "SELECT stage FROM progress WHERE student_id=?", (student,)
        ).fetchone()[0]
        assert stage == ("assessment" if committed else "learning")


def test_sigterm_drains_inflight_submit(servers, tmp_path):
    server = servers(tmp_path).start()
    body = server.prepare("graceful")
    with concurrent.futures.ThreadPoolExecutor() as pool:
        request = pool.submit(
            server.client.post, "/submit", json=body, headers={"X-Test-Hold": "1"}
        )
        wait_for(server.marker.exists)
        server.proc.send_signal(signal.SIGTERM)
        wait_for(lambda: "Shutting down" in server.logs())
        time.sleep(0.3)  # keep the request pending through the server drain phase
        server.marker.with_suffix(".release").touch()
        assert request.result(timeout=10).status_code == 200
    server.proc.wait(timeout=10)
    # Uvicorn re-raises the original signal after completing shutdown.
    assert server.proc.returncode in (0, -signal.SIGTERM), server.logs()
    assert '"message": "graceful_stop"' in server.logs()
    assert_answer(server.path, "graceful", True)


def test_docker_style_sigkill_after_drain_timeout_is_atomic(servers, tmp_path):
    server = servers(tmp_path, TEST_PAUSE_STUDENT="timeout").start()
    body = server.prepare("timeout")
    with concurrent.futures.ThreadPoolExecutor() as pool:
        request = pool.submit(server.client.post, "/submit", json=body)
        wait_for(server.marker.exists)
        server.proc.send_signal(signal.SIGTERM)
        wait_for(
            lambda: "timeout graceful shutdown exceeded" in server.logs(), timeout=8
        )
        # Simulate Docker's final kill, since cancellation cannot stop a sync thread.
        if server.proc.poll() is None:
            server.proc.kill()
        server.proc.wait(timeout=5)
        try:
            assert request.result(timeout=5).status_code != 200
        except httpx.TransportError:
            pass
    servers(tmp_path).start()
    assert_answer(server.path, "timeout", False)


def test_sigkill_recovers_acknowledged_wal_and_rolls_back_pending_submit(
    servers, tmp_path
):
    server = servers(tmp_path, TEST_KEEP_WAL="1", TEST_PAUSE_STUDENT="pending").start()
    acknowledged = []
    for number in range(10):
        student = f"ack{number}"
        response = server.client.post("/submit", json=server.prepare(student))
        assert response.status_code == 200
        acknowledged.append(student)
    body = server.prepare("pending")
    with concurrent.futures.ThreadPoolExecutor() as pool:
        request = pool.submit(server.client.post, "/submit", json=body)
        wait_for(server.marker.exists)
        assert Path(str(server.path) + "-wal").stat().st_size > 0
        server.proc.kill()
        server.proc.wait(timeout=5)
        with pytest.raises(httpx.TransportError):
            request.result(timeout=5)
    restarted = servers(tmp_path).start()
    assert '"wal_present": true' in restarted.logs()
    for student in acknowledged:
        assert_answer(server.path, student, True)
    assert_answer(server.path, "pending", False)


def test_corrupt_database_serves_diagnostics_without_initialization(servers, tmp_path):
    path = tmp_path / "grading.db"
    original = b"not a SQLite database" * 100
    path.write_bytes(original)
    server = servers(tmp_path, TEST_REFUSE_INIT="1").start()
    assert server.client.get("/livez").status_code == 200
    assert server.client.get("/healthz").status_code == 503
    response = server.client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["database_integrity"]["ok"] is False
    assert (
        server.client.post(
            "/session",
            json={"student_id": "x", "session_id": "x"},
            headers={"X-Coordinator-Key": KEY},
        ).status_code
        == 503
    )
    assert server.logs().count('"message": "db_integrity_failed"') == 1
    assert not server.marker.exists()  # stronger than merely checking no backup
    assert not list(tmp_path.glob("*.bak"))
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "pause_sql",
    [
        "DROP TABLE cases",
        "INSERT INTO submissions",
        "ALTER TABLE submissions ADD COLUMN ground_truth_modifier_s",
        "UPDATE progress SET case_assigned_at",
    ],
)
def test_sigkill_during_table_swap_restores_old_schema_and_backup(
    servers, tmp_path, pause_sql
):
    spec = importlib.util.spec_from_file_location(
        "rehearse", ROOT / "scripts/rehearse-migration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "grading.db"
    module.build_synthetic(str(path))
    if pause_sql == "INSERT INTO submissions":
        # Force the pre-UNIQUE rebuild path with the same synthetic rows.
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(
                "ALTER TABLE submissions RENAME TO old; "
                "CREATE TABLE submissions AS SELECT * FROM old; DROP TABLE old;"
            )
    if pause_sql == "UPDATE progress SET case_assigned_at":
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(
                "ALTER TABLE progress RENAME TO old; "
                "CREATE TABLE progress AS SELECT student_id, stage, case_order_index FROM old; DROP TABLE old;"
            )
    server = servers(tmp_path, TEST_MIGRATION_PAUSE=pause_sql)
    wait_for(server.marker.exists)
    server.proc.kill()
    server.proc.wait(timeout=5)
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    with (
        closing(sqlite3.connect(path)) as conn,
        closing(sqlite3.connect(backups[0])) as backup,
    ):
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name='cases_new'"
        ).fetchall()
        assert (
            conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
            == backup.execute(
                "SELECT name, sql FROM sqlite_master ORDER BY name"
            ).fetchall()
        )
        assert (
            conn.execute("SELECT * FROM cases").fetchall()
            == backup.execute("SELECT * FROM cases").fetchall()
        )
        assert (
            conn.execute("SELECT * FROM submissions").fetchall()
            == backup.execute("SELECT * FROM submissions").fetchall()
        )
    # Wait out the backup filename's one-second timestamp before restarting migration.
    time.sleep(1.1)
    servers(tmp_path).start()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "version" in [r[1] for r in conn.execute("PRAGMA table_info(cases)")]
    # Restore the backup to a separate disposable database and migrate it too.
    restored = tmp_path / "restored"
    restored.mkdir()
    with (
        closing(sqlite3.connect(backups[0])) as source,
        closing(sqlite3.connect(restored / "grading.db")) as target,
    ):
        source.backup(target)
    servers(restored).start()
    with closing(sqlite3.connect(restored / "grading.db")) as conn:
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
