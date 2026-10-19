"""Health endpoints and the telemetry snapshot (issue #113).

Three separable questions, and the properties that make them worth having:

  * /livez answers when nothing else does -- it must never touch the database;
  * /healthz keeps its old, narrow meaning (a `depends_on: service_healthy`
    consumer would otherwise inherit a dependency outage as its own failure);
  * /readyz actually distinguishes "readable" from "writable" -- the gap that
    lets a read-only volume or a squeezed disk serve 500s on every /submit
    while a read-only probe stays green.

Plus the counter that did not exist before: a SQLite writer-lock timeout is
now a number, not just a 5 s latency spike in an access log.
"""

import http.server
import logging
import os
import socket
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app import health as health_module
from app import main as main_module
from app import telemetry
from app.main import app


def _case_id(client, token):
    return client.get("/case", headers={"X-Grading-Token": token}).json()["case_id"]


def _closed_port() -> str:
    """A URL whose port is bound-then-released, i.e. nothing listens: the
    probe's "down" path, with no race against a server still shutting down."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


class _StatusServer(http.server.HTTPServer):
    """A tiny real HTTP server: the Orthanc probe is urllib, so an honest
    probe test needs a socket, not a mock of urllib."""

    def __init__(self, status: int):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.status = status
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.shutdown()
        self.server_close()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib naming
        self.send_response(self.server.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # keep the test output clean
        pass


# ---- /livez ----


def test_livez_answers_and_does_not_touch_the_database(client, monkeypatch):
    """Liveness is the one check that must survive a dead database. Proved by
    making the database unusable: /livez still answers, /healthz must not."""
    monkeypatch.setattr(db_module, "get_connection", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("down")))
    resp = client.get("/livez")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["uptime_seconds"] >= 0
    assert client.get("/healthz").status_code == 503


# ---- /healthz stays narrow ----


def test_healthz_keeps_its_old_contract(client):
    """No dependency probing, no write probing: exactly the response the
    compose healthchecks and `viewer`'s depends_on already consume."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---- /readyz ----


def test_readyz_is_ready_without_a_configured_dependency(client, monkeypatch):
    monkeypatch.setenv("GRADING_ORTHANC_URL", "")
    resp = client.get("/readyz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database_read"]["ok"] is True
    assert body["checks"]["database_write"]["ok"] is True
    assert body["checks"]["storage"]["ok"] is True
    assert body["checks"]["dependency:orthanc"] == {"ok": None, "skipped": True}
    assert "grading_dependency_up" not in {sample["name"] for sample in telemetry.signals()["gauges"]}


def test_readyz_write_probe_actually_writes(client):
    client.get("/readyz")
    conn = db_module.get_connection()
    try:
        row = conn.execute("SELECT touched_at FROM telemetry_probe WHERE id = 1").fetchone()
    finally:
        conn.close()
    assert row is not None and row["touched_at"] > 0


def test_readyz_is_503_when_the_database_cannot_be_written(client):
    """The exact failure /healthz cannot see (read-only volume, full disk): the
    client gets 503 + the exception CLASS, never the database path (#62)."""
    conn = db_module.get_connection()
    try:
        conn.execute("DROP TABLE telemetry_probe")
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database_read"]["ok"] is True
    assert body["checks"]["database_write"]["ok"] is False
    assert body["checks"]["database_write"]["error"] == "OperationalError"
    assert str(db_module.DB_PATH) not in resp.text


def test_readyz_is_503_when_storage_is_below_the_floor(client, monkeypatch):
    monkeypatch.setenv("GRADING_STORAGE_MIN_FREE_BYTES", str(1 << 62))
    resp = client.get("/readyz")
    assert resp.status_code == 503
    check = resp.json()["checks"]["storage"]
    assert check["ok"] is False
    assert check["free_bytes"] < check["min_free_bytes"]


def test_readyz_survives_a_statvfs_failure(client, monkeypatch):
    monkeypatch.setattr(health_module.os, "statvfs", lambda _path: (_ for _ in ()).throw(OSError("nope")))
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["storage"]["error"] == "OSError"


@pytest.mark.parametrize("status,expected_up", [(200, True), (401, True), (500, True)])
def test_orthanc_probe_treats_any_http_answer_as_reachable(client, status, expected_up):
    """Orthanc has AuthenticationEnabled=true and this service holds no Orthanc
    credentials, so 401 must count as up. Only a connection error is "down"."""
    with _StatusServer(status) as server:
        result = health_module._probe_orthanc(server.url)
    assert result["ok"] is expected_up
    assert result.get("detail") == f"http_{status}"


def test_orthanc_probe_reports_a_closed_port_as_down(client):
    result = health_module._probe_orthanc(_closed_port())
    assert result["ok"] is False
    assert result["error"] in {"URLError", "ConnectionRefusedError", "timeout"}


def test_readyz_reports_degraded_not_503_when_only_a_dependency_is_down(client, monkeypatch):
    """A partial degradation must not look like a total outage: grading works
    without Orthanc, and /healthz must stay green for the viewer's depends_on."""
    monkeypatch.setenv("GRADING_ORTHANC_URL", _closed_port())
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
    assert resp.json()["checks"]["dependency:orthanc"]["ok"] is False
    assert telemetry.get_gauge(telemetry.DEPENDENCY_UP, {"dependency": "orthanc"}) == 0
    assert client.get("/healthz").status_code == 200


def test_readyz_caches_and_marks_a_stale_verdict(client, monkeypatch):
    monkeypatch.setenv("GRADING_HEALTH_CACHE_SECONDS", "60")
    first = client.get("/readyz").json()
    second = client.get("/readyz").json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["age_seconds"] >= 0
    assert first["status"] == second["status"]


def test_readyz_force_bypasses_the_cache(client):
    health_module.readiness(force=True)
    cached = health_module.readiness()
    assert cached[0]["cached"] is True
    forced = health_module.readiness(force=True)
    assert forced[0]["cached"] is False


def test_readyz_cache_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("GRADING_HEALTH_CACHE_SECONDS", "0")
    client.get("/readyz")
    assert client.get("/readyz").json()["cached"] is False


def test_readyz_reports_a_broken_database_connection_without_leaking_it(client, monkeypatch):
    monkeypatch.setattr(
        db_module, "get_connection", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("unable to open x"))
    )
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["checks"]["database_read"]["error"] == "OperationalError"
    assert "unable to open" not in resp.text


# ---- /internal/signals ----


def test_internal_signals_is_not_on_the_public_api_prefix():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/internal/signals" in paths
    assert not any(path and path.startswith("/api/") for path in paths)


def test_internal_signals_reports_requests_and_histograms(client):
    client.get("/livez")
    body = client.get("/internal/signals").json()
    assert body["db_available"] is True
    requests = [s for s in body["counters"] if s["name"] == telemetry.REQUESTS_TOTAL]
    assert requests, body["counters"]
    livez = [s for s in requests if s["labels"]["path"] == "/livez"]
    assert livez and livez[0]["labels"]["status_class"] == "2xx"
    assert livez[0]["value"] >= 1

    histogram = [h for h in body["histograms"] if h["name"] == telemetry.REQUEST_DURATION_MS]
    assert histogram and histogram[0]["count"] >= 1
    assert [bucket["le"] for bucket in histogram[0]["buckets"]] == list(telemetry.REQUEST_DURATION_BUCKETS_MS)


def test_internal_signals_exposes_storage_and_session_gauges(client, mint_token):
    mint_token("stu_signals")
    body = client.get("/internal/signals").json()
    gauges = {sample["name"] for sample in body["gauges"]}
    assert {telemetry.SESSIONS_ACTIVE, telemetry.DB_SIZE_BYTES, telemetry.VOLUME_FREE_BYTES} <= gauges
    assert telemetry.LAST_SESSION_MINT_UNIXTIME in gauges
    assert body["summary"]["sessions_active"] == 1


def test_internal_signals_carries_no_identifying_fields(client, mint_token):
    token = mint_token("stu_private_name")
    client.get("/case", headers={"X-Grading-Token": token})
    text = client.get("/internal/signals").text
    assert token not in text
    assert "stu_private_name" not in text


def test_internal_signals_survives_a_dead_database(client, monkeypatch):
    """DoD 11: "the probe works and reports a broken database" must be visible,
    not turned into the same 503 as every other failure."""
    monkeypatch.setattr(
        db_module, "get_connection", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("cannot open"))
    )
    resp = client.get("/internal/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db_available"] is False
    assert telemetry.SESSIONS_ACTIVE not in {sample["name"] for sample in body["gauges"]}


def test_signals_work_without_a_connection_at_all():
    body = telemetry.signals(None)
    assert body["db_available"] is False
    assert body["summary"]["db_size_bytes"] is None


# ---- Counters ----


def test_submit_records_a_submission_and_write_latency(client, mint_token):
    token = mint_token("stu_write_signal")
    case_id = _case_id(client, token)
    resp = client.post(
        "/submit",
        json={"token": token, "case_id": case_id, "stage": "learning", "text": "not logged anywhere"},
    )
    assert resp.status_code == 200

    body = client.get("/internal/signals").json()
    submissions = [s for s in body["counters"] if s["name"] == telemetry.SUBMISSIONS_TOTAL]
    assert submissions and submissions[0]["labels"]["stage"] == "learning"
    writes = [h for h in body["histograms"] if h["name"] == telemetry.DB_WRITE_DURATION_MS]
    assert writes and writes[0]["labels"]["operation"] == "submit"
    assert writes[0]["count"] == 1
    assert "not logged anywhere" not in client.get("/internal/signals").text


def test_db_lock_timeout_on_the_500_path_increments_the_counter(client, mint_token, monkeypatch):
    """Issue #113's own acceptance criterion: the `database is locked` 500 that
    #70 documented raises a counter, not just a traceback."""
    token = mint_token("stu_lock_signal")

    def _locked(conn, student_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "_get_or_create_progress", _locked)
    with TestClient(app, raise_server_exceptions=False) as non_raising:
        resp = non_raising.get("/case", headers={"X-Grading-Token": token})
    assert resp.status_code == 500
    assert telemetry.counter_value(telemetry.DB_LOCK_ERRORS_TOTAL) == 1

    signals = client.get("/internal/signals").json()
    assert [s for s in signals["counters"] if s["name"] == telemetry.DB_LOCK_ERRORS_TOTAL][0]["value"] == 1
    assert signals["summary"]["db_lock_errors_total"] == 1


def test_readiness_write_probe_survives_a_connection_that_cannot_roll_back(client, monkeypatch):
    """The verdict must not depend on cleanup succeeding: if the rollback
    itself throws, the write check is still reported failed instead of the
    health endpoint blowing up inside its own error handling."""

    class UnrollableConnection:
        def execute(self, sql, *_args):
            if "telemetry_probe" in sql:
                raise sqlite3.OperationalError("database is locked")
            return self

        def fetchone(self):
            return (1,)

        def commit(self):
            return None

        def rollback(self):
            raise sqlite3.ProgrammingError("closed")

        def close(self):
            return None

    monkeypatch.setattr(db_module, "get_connection", lambda: UnrollableConnection())
    result, ok = health_module.readiness(force=True)
    assert ok is False
    assert result["checks"]["database_write"]["ok"] is False
    assert telemetry.counter_value(telemetry.DB_LOCK_ERRORS_TOTAL) == 1


def test_lock_recognition_is_not_fooled_by_other_operational_errors():
    assert telemetry.is_db_lock_error(sqlite3.OperationalError("database is locked")) is True
    assert telemetry.is_db_lock_error(sqlite3.OperationalError("database table is locked")) is True
    assert telemetry.is_db_lock_error(sqlite3.OperationalError("no such table: x")) is False
    assert telemetry.is_db_lock_error(sqlite3.IntegrityError("locked")) is False
    assert telemetry.is_db_lock_error(RuntimeError("database is locked")) is False


def test_readiness_write_probe_counts_its_own_lock_timeout(client, monkeypatch):
    """The second, independent source of the same counter: the probe hits a
    lock and the number moves even if no request ever 500s."""
    from app import health as health_mod

    real_connect = db_module.get_connection
    inner = real_connect()

    class LockedConnection:
        def execute(self, sql, *args):
            if "telemetry_probe" in sql:
                raise sqlite3.OperationalError("database is locked")
            return inner.execute(sql, *args)

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            inner.close()

    monkeypatch.setattr(db_module, "get_connection", lambda: LockedConnection())
    result, ok = health_mod.readiness(force=True)
    assert ok is False
    assert result["checks"]["database_write"]["ok"] is False
    assert telemetry.counter_value(telemetry.DB_LOCK_ERRORS_TOTAL) == 1


# ---- Bounded labels ----


def test_path_label_is_bounded_against_arbitrary_paths(client):
    client.get("/definitely-not-a-route")
    body = client.get("/internal/signals").json()
    labels = {s["labels"]["path"] for s in body["counters"] if s["name"] == telemetry.REQUESTS_TOTAL}
    assert "<other>" in labels
    assert "/definitely-not-a-route" not in labels


# ---- The periodic snapshot line ----


def test_snapshot_tick_logs_the_flat_summary(client, caplog):
    conn = db_module.get_connection()
    try:
        with caplog.at_level(logging.INFO):
            snapshot = telemetry.emit_snapshot(conn)
    finally:
        conn.close()
    records = [r for r in caplog.records if r.getMessage() == "telemetry_snapshot"]
    assert records, "no telemetry_snapshot line"
    payload = records[-1].__dict__
    for field in snapshot["summary"]:
        assert field in payload, field


def test_snapshot_tick_never_raises(client, monkeypatch, caplog):
    monkeypatch.setattr(db_module, "get_connection", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
    with caplog.at_level(logging.ERROR):
        telemetry._snapshot_tick()
    assert any(r.getMessage() == "telemetry_snapshot_failed" for r in caplog.records)


def test_snapshot_thread_emits_and_stops(client, monkeypatch, caplog):
    monkeypatch.setenv("GRADING_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS", "0.05")
    with caplog.at_level(logging.INFO):
        stop = telemetry.start_snapshot_thread()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(r.getMessage() == "telemetry_snapshot" for r in caplog.records):
                break
            time.sleep(0.01)
        stop()
    assert any(r.getMessage() == "telemetry_snapshot" for r in caplog.records)
    assert not [t for t in threading.enumerate() if t.name == "telemetry-snapshot"]


def test_snapshot_thread_is_disabled_by_zero(client, monkeypatch):
    monkeypatch.setenv("GRADING_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS", "0")
    stop = telemetry.start_snapshot_thread()
    stop()
    assert not [t for t in threading.enumerate() if t.name == "telemetry-snapshot"]


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 30.0), ("", 30.0), ("0", 0.0), ("15", 15.0), ("0.05", 0.05), ("nonsense", 30.0), ("-5", 0.0)],
)
def test_snapshot_interval_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(telemetry.SNAPSHOT_INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(telemetry.SNAPSHOT_INTERVAL_ENV, raw)
    assert telemetry.snapshot_interval_seconds() == expected


@pytest.mark.parametrize("name", ["GRADING_HEALTH_CACHE_SECONDS", "GRADING_STORAGE_MIN_FREE_BYTES"])
def test_invalid_health_config_falls_back_instead_of_crashing(monkeypatch, name, caplog):
    monkeypatch.setenv(name, "not-a-number")
    with caplog.at_level(logging.WARNING):
        value = health_module._env_int(name, 42)
    assert value == 42
    assert any(r.getMessage() == "health_config_invalid" for r in caplog.records)


def test_snapshot_interval_invalid_is_logged(monkeypatch, caplog):
    monkeypatch.setenv(telemetry.SNAPSHOT_INTERVAL_ENV, "later")
    with caplog.at_level(logging.WARNING):
        assert telemetry.snapshot_interval_seconds() == 30.0
    assert any(r.getMessage() == "telemetry_snapshot_interval_invalid" for r in caplog.records)


# ---- Registry primitives ----


def test_gauges_and_counters_are_labelled_independently():
    telemetry.reset()
    telemetry.set_gauge("g", 1)
    telemetry.set_gauge("g", 2, {"k": "v"})
    assert telemetry.get_gauge("g") == 1
    assert telemetry.get_gauge("g", {"k": "v"}) == 2
    assert telemetry.get_gauge("absent") is None

    telemetry.inc_counter("c", {"k": "a"})
    telemetry.inc_counter("c", {"k": "a"}, amount=2)
    telemetry.inc_counter("c", {"k": "b"})
    assert telemetry.counter_value("c", {"k": "a"}) == 3
    assert telemetry.counter_value("c", {"k": "b"}) == 1
    assert telemetry.counter_value("missing") == 0


def test_histogram_buckets_are_cumulative():
    telemetry.reset()
    for value in (1.0, 7.0, 60.0):
        telemetry.observe_histogram("h", value, (5, 10, 100))
    sample = telemetry.signals()["histograms"][0]
    assert [bucket["count"] for bucket in sample["buckets"]] == [1, 2, 3]
    assert sample["count"] == 3
    assert sample["sum"] == 68.0


def test_reset_clears_counters_gauges_and_histograms():
    telemetry.inc_counter("x")
    telemetry.set_gauge("y", 1)
    telemetry.observe_histogram("z", 1.0, (5,))
    telemetry.reset()
    body = telemetry.signals()
    assert body["counters"] == [] and body["histograms"] == []
    # signals() stamps these two on every build by definition; nothing else survives.
    assert {sample["name"] for sample in body["gauges"]} == {
        telemetry.PROCESS_START_UNIXTIME,
        telemetry.TELEMETRY_LAST_SUCCESS_UNIXTIME,
    }


def test_db_gauge_failure_is_logged_not_raised(client, caplog):
    conn = db_module.get_connection()
    try:
        conn.execute("DROP TABLE sessions")
        conn.commit()
        with caplog.at_level(logging.ERROR):
            body = telemetry.signals(conn)
    finally:
        conn.close()
    assert body["db_available"] is True
    assert any(r.getMessage() == "telemetry_db_gauge_failed" for r in caplog.records)


def test_file_size_and_free_bytes_report_none_on_error(client, monkeypatch):
    monkeypatch.setattr(telemetry.os, "stat", lambda _path: (_ for _ in ()).throw(OSError("gone")))
    monkeypatch.setattr(telemetry.os, "statvfs", lambda _path: (_ for _ in ()).throw(OSError("gone")))
    conn = db_module.get_connection()
    try:
        body = telemetry.signals(conn)
    finally:
        conn.close()
    assert telemetry.DB_SIZE_BYTES not in {sample["name"] for sample in body["gauges"]}
    assert telemetry.VOLUME_FREE_BYTES not in {sample["name"] for sample in body["gauges"]}


def test_summary_tolerates_absent_gauges():
    """A snapshot taken before any /readyz ever ran has no readiness gauges;
    the flat log line must still be shaped, not crash on None."""
    body = telemetry.signals(None)
    assert body["summary"]["readyz_ok"] is None
    assert body["summary"]["dependency_orthanc_up"] is None
    assert body["summary"]["last_submission_age_seconds"] is None


def test_snapshot_interval_env_is_the_documented_name():
    assert telemetry.SNAPSHOT_INTERVAL_ENV == "GRADING_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS"
    assert os.environ.get("GRADING_ORTHANC_URL") == ""


def test_readyz_reports_a_failing_read(client, monkeypatch):
    """A connection that opens but cannot answer SELECT 1 (corruption, I/O
    error) is a read failure, distinct from "cannot open at all"."""

    class BrokenConnection:
        def execute(self, _sql, *_args):
            raise sqlite3.DatabaseError("disk image is malformed")

        def commit(self):
            raise sqlite3.DatabaseError("disk image is malformed")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(db_module, "get_connection", lambda: BrokenConnection())
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["checks"]["database_read"]["error"] == "DatabaseError"
    assert "malformed" not in resp.text
