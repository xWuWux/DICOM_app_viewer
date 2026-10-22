"""Startup gate and checkpoint unit contracts; real signal tests live in scripts/."""

import sqlite3
import time
from contextlib import closing
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import db, health, telemetry
from app.main import app


@pytest.fixture(autouse=True)
def isolated_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "STARTUP_INTEGRITY_OK", None)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "grading.db"))
    health.reset_cache()
    yield
    health.reset_cache()


def test_full_is_set_on_every_connection():
    assert db.check_startup_integrity()
    for _ in range(2):
        with closing(db.get_connection()) as conn:
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_corrupt_start_skips_init_and_blocks_traffic(monkeypatch, tmp_path, caplog):
    path = tmp_path / "grading.db"
    path.write_bytes(b"invalid database" * 100)
    init = MagicMock()
    monkeypatch.setattr(db, "init_db", init)
    with TestClient(app) as client:
        init.assert_not_called()
        assert client.get("/livez").status_code == 200
        assert client.get("/healthz").status_code == 503
        assert client.get("/readyz").json()["checks"]["database_integrity"] == {"ok": False}
        response = client.post("/submit", json={})
        assert response.status_code == 503
        assert response.json()["error_code"] == "DATABASE_UNAVAILABLE"
        assert telemetry.get_gauge(telemetry.DB_INTEGRITY_OK) == 0
        assert str(path) not in response.text
    assert sum(r.message == "db_integrity_failed" for r in caplog.records) == 1
    assert db.shutdown_checkpoint() == "skipped"


def test_non_ok_check_result_fails_closed_and_closes(monkeypatch, tmp_path):
    (tmp_path / "grading.db").touch()
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [("inconsistent page",)]
    monkeypatch.setattr(db.sqlite3, "connect", lambda *a, **k: conn)
    assert db.check_startup_integrity() is False
    conn.close.assert_called_once()
    with pytest.raises(sqlite3.DatabaseError):
        db.get_connection()


def test_gate_is_cached_until_restart(monkeypatch):
    assert db.check_startup_integrity()
    db.init_db()
    # Further probes never invoke quick_check; a new lifespan rechecks it.
    monkeypatch.setattr(db, "check_startup_integrity", lambda: pytest.fail("probe reran startup check"))
    assert health.readiness(force=True)[1]
    assert health.readiness(force=True)[1]


def test_checkpoint_failure_is_best_effort(caplog):
    assert db.shutdown_checkpoint() == "failed"  # missing file, never creates one
    assert any(r.message == "db_checkpoint_failed" for r in caplog.records)


def test_checkpoint_busy_has_short_lock_wait():
    assert db.check_startup_integrity()
    with closing(db.get_connection()) as writer, closing(db.get_connection()) as reader:
        writer.execute("CREATE TABLE probe(value)")
        writer.execute("INSERT INTO probe VALUES(1)")
        writer.commit()
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM probe").fetchall()
        writer.execute("INSERT INTO probe VALUES(2)")
        writer.commit()
        started = time.monotonic()
        assert db.shutdown_checkpoint() == "busy"
        assert time.monotonic() - started < 1.0
        reader.rollback()
        assert db.shutdown_checkpoint() == "ok"
