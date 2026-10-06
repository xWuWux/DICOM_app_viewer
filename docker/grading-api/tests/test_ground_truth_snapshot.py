"""
Tests for frozen/versioned ground truth (issue #96).

Covers the reviewer's acceptance list:
  (a) fresh schema carries the new columns + freeze trigger,
  (b) /submit freezes ground_truth_category / ground_truth_modifier_s /
      case_version in the SAME transaction as is_correct,
  (c) UPDATE of a submitted case's GT is rejected (trigger); legal
      change = new version row; already-recorded /results NEVER move,
  (d) legacy-database migration: old schema with data -> new schema,
      backfill (gt_backfilled=1), pre-migration backup file, idempotent
      second start, and an interrupted rebuild leaving data intact.

Every test here runs against a temp file only -- the migration is never
executed against a real grading.db (CLAUDE.md stop condition; real-data
sign-off tracked in the issue).
"""

import glob
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as db_module

FUTURE = 2**40  # far-future expires_at for hand-inserted sessions


def _walk_to_complete(client, token):
    """Submit every remaining case in order (seed has exactly one case
    per stage): learning(text) -> assessment(3, wrong on purpose? no --
    correct 3) -> test(4A)."""
    for expected_stage, body in (
        ("learning", {"text": "note"}),
        ("assessment", {"category": "3", "modifier_s": False}),
        ("test", {"category": "4A", "modifier_s": True}),
    ):
        case = client.get(f"/case?token={token}").json()
        assert case["complete"] is False and case["stage"] == expected_stage
        resp = client.post("/submit", json={"token": token, "case_id": case["case_id"], "stage": case["stage"], **body})
        assert resp.status_code == 200, resp.text


# ---- (a) fresh schema ----


def test_fresh_schema_has_version_column_gt_snapshot_columns_and_trigger(client):
    conn = db_module.get_connection()
    try:
        case_cols = {row[1] for row in conn.execute("PRAGMA table_info(cases)").fetchall()}
        sub_cols = {row[1] for row in conn.execute("PRAGMA table_info(submissions)").fetchall()}
        assert {"version"} <= case_cols
        assert {"ground_truth_category", "ground_truth_modifier_s", "case_version", "gt_backfilled"} <= sub_cols
        trigger = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='cases_ground_truth_frozen'"
        ).fetchone()
        assert trigger is not None
    finally:
        conn.close()


# ---- (b) freeze at /submit ----


def test_submit_freezes_gt_snapshot_and_version(client, mint_token):
    token = mint_token("stu_freeze")
    _walk_to_complete(client, token)
    conn = db_module.get_connection()
    try:
        row = conn.execute(
            "SELECT ground_truth_category, ground_truth_modifier_s, case_version, gt_backfilled, is_correct "
            "FROM submissions WHERE student_id = ? AND stage = 'test'",
            ("stu_freeze",),
        ).fetchone()
        # seeded test case: GT 4A, modifier S=1, first version, proven snapshot.
        assert row["ground_truth_category"] == "4A"
        assert row["ground_truth_modifier_s"] == 1
        assert row["case_version"] == 1
        assert row["gt_backfilled"] == 0
        assert row["is_correct"] == 1
    finally:
        conn.close()


# ---- (c) freeze enforcement + stable /results ----


def _raw_update_gt(db_path, category):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE cases SET ground_truth_category = ? WHERE stage = 'test'", (category,))
        conn.commit()
    finally:
        conn.close()


def test_gt_update_rejected_once_submissions_exist(client, mint_token):
    token = mint_token("stu_frozen")
    _walk_to_complete(client, token)
    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        _raw_update_gt(db_module.DB_PATH, "4B")
    assert "ground truth frozen" in str(excinfo.value)


def test_gt_update_allowed_before_any_submission(client):
    conn = db_module.get_connection()
    try:
        conn.execute("UPDATE cases SET ground_truth_category = '4B' WHERE stage = 'learning'")
        conn.commit()
    finally:
        conn.close()


def test_results_never_move_after_legal_gt_change(client, mint_token):
    """The issue's own acceptance criterion, via the LEGAL path: a new
    version row (trigger forbids the illegal direct UPDATE). Old answers
    keep the old version's GT; accuracy and breakdown are byte-stable."""
    token = mint_token("stu_stable")
    _walk_to_complete(client, token)
    before = client.get(f"/results?token={token}").json()
    assert before["accuracy"] == 1.0

    conn = db_module.get_connection()
    try:
        test_case = conn.execute("SELECT * FROM cases WHERE stage = 'test'").fetchone()
        conn.execute(
            "INSERT INTO cases (stage, order_index, orthanc_study_uid, title, ground_truth_category, "
            "ground_truth_modifier_s, reference_report, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                test_case["stage"],
                test_case["order_index"],
                test_case["orthanc_study_uid"],
                test_case["title"],
                "4B",
                0,
                test_case["reference_report"],
                2,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    after = client.get(f"/results?token={token}").json()
    assert after == before
    # property: no row may show a disagreement while claiming correctness
    for row in after["breakdown"]:
        if row["ground_truth"] != row["submitted"]:
            assert row["correct"] is False

    # ... and a NEW student now faces version 2's 4B as the current truth
    token2 = mint_token("stu_newer")
    _walk_to_complete(client, token2)  # submits 4A -> now WRONG against 4B
    conn = db_module.get_connection()
    try:
        row = conn.execute(
            "SELECT s.is_correct, s.ground_truth_category, s.case_version FROM submissions s "
            "WHERE s.student_id = 'stu_newer' AND s.stage = 'test'"
        ).fetchone()
        assert row["ground_truth_category"] == "4B"
        assert row["case_version"] == 2
        assert row["is_correct"] == 0
    finally:
        conn.close()


# ---- (d) migration of an existing legacy database ----


def _create_legacy_db(path, *, reference_report_column=True):
    """Exact pre-#96 schema (cases UNIQUE(stage, order_index),
    submissions WITHOUT the four new columns but WITH the #28 UNIQUE, so
    only the ALTER/backfill branch runs), plus data."""
    conn = sqlite3.connect(path)
    rr = ", reference_report TEXT NOT NULL" if reference_report_column else ""
    conn.executescript(
        f"""
        CREATE TABLE cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            orthanc_study_uid TEXT NOT NULL,
            title TEXT NOT NULL,
            ground_truth_category TEXT NOT NULL,
            ground_truth_modifier_s INTEGER NOT NULL DEFAULT 0{rr},
            UNIQUE(stage, order_index)
        );
        CREATE TABLE progress (
            student_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            case_order_index INTEGER NOT NULL,
            case_assigned_at REAL NOT NULL
        );
        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            case_id INTEGER NOT NULL REFERENCES cases(id),
            stage TEXT NOT NULL,
            submitted_category TEXT,
            submitted_modifier_s INTEGER,
            submitted_text TEXT,
            is_correct INTEGER,
            time_spent_seconds REAL,
            submitted_at REAL NOT NULL,
            UNIQUE(student_id, case_id, stage)
        );
        CREATE TABLE sessions (
            token TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        """
    )
    cols = "stage, order_index, orthanc_study_uid, title, ground_truth_category, ground_truth_modifier_s"
    vals = "'test', 0, '1.2.3', 'Test 1', '4A', 1"
    if reference_report_column:
        cols += ", reference_report"
        vals += ", 'ref'"
    conn.execute(f"INSERT INTO cases ({cols}) VALUES ({vals})")
    conn.execute(
        "INSERT INTO progress (student_id, stage, case_order_index, case_assigned_at)"
        " VALUES ('stu_old', 'complete', 0, 1.0)"
    )
    conn.execute(
        "INSERT INTO submissions (student_id, case_id, stage, submitted_category,"
        " submitted_modifier_s, submitted_text, is_correct, time_spent_seconds, submitted_at)"
        " VALUES ('stu_old', 1, 'test', '4A', 1, 'was private', 1, 10, 1.0)"
    )
    conn.execute(
        "INSERT INTO sessions (token, student_id, session_id, created_at, expires_at)"
        " VALUES ('tok-old', 'stu_old', 's', 1.0, ?)",
        (FUTURE,),
    )
    conn.commit()
    conn.close()


def test_legacy_db_migrates_with_backup_and_backfill(tmp_path, monkeypatch):
    db_path = str(tmp_path / "grading-legacy.db")
    _create_legacy_db(db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    db_module.init_db()

    assert glob.glob(f"{db_path}.pre-migration-*.sqlite.bak"), "pre-migration backup missing"
    conn = sqlite3.connect(db_path)
    try:
        sub_cols = {row[1] for row in conn.execute("PRAGMA table_info(submissions)")}
        assert {"ground_truth_category", "ground_truth_modifier_s", "case_version", "gt_backfilled"} <= sub_cols
        (case_version, gt, gt_mod, backfilled, answer_text) = conn.execute(
            "SELECT case_version, ground_truth_category, ground_truth_modifier_s,"
            " gt_backfilled, submitted_text FROM submissions"
        ).fetchone()
        assert (case_version, gt, gt_mod, backfilled) == (1, "4A", 1, 1)
        assert answer_text == "was private"  # data preserved through the ALTER
        case_cols = {row[1] for row in conn.execute("PRAGMA table_info(cases)")}
        assert "version" in case_cols
        trigger = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='cases_ground_truth_frozen'"
        ).fetchone()
        assert trigger is not None
        # the migrated database enforces the freeze end to end
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE cases SET ground_truth_category = '4B' WHERE stage = 'test'")
    finally:
        conn.close()


def test_migrated_db_results_served_from_snapshot(tmp_path, monkeypatch):
    db_path = str(tmp_path / "grading-legacy.db")
    _create_legacy_db(db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()

    from app.main import app

    with TestClient(app) as tc:
        results = tc.get("/results?token=tok-old").json()
    assert results == {
        "complete": True,
        "test_total": 1,
        "test_correct": 1,
        "accuracy": 1.0,
        "breakdown": [{"ground_truth": "4A", "submitted": "4A", "correct": True}],
    }


def test_second_init_on_current_db_is_noop_and_skips_backup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "grading-legacy.db")
    _create_legacy_db(db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    db_module.init_db()
    backups_after_first = glob.glob(f"{db_path}.pre-migration-*.sqlite.bak")
    assert len(backups_after_first) == 1

    db_module.init_db()  # already current: no rewrite, no second copy
    assert glob.glob(f"{db_path}.pre-migration-*.sqlite.bak") == backups_after_first


def test_interrupted_cases_rebuild_leaves_database_intact(tmp_path, monkeypatch, caplog):
    """An old cases table that the rebuild's explicit column list can't
    satisfy (here: no reference_report column) must abort the WHOLE
    transaction -- cases comes back intact under its original name and
    shape, data readable, startup exits loudly (SystemExit)."""
    db_path = str(tmp_path / "grading-broken.db")
    _create_legacy_db(db_path, reference_report_column=False)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    with pytest.raises(SystemExit):
        db_module.init_db()

    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cases)")}
        assert "reference_report" not in cols  # original shape, not the new one
        assert conn.execute("SELECT ground_truth_category FROM cases WHERE stage = 'test'").fetchone()[0] == "4A"
        legacy = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert ("cases",) in legacy and ("cases_old",) not in legacy
    finally:
        conn.close()
    assert glob.glob(f"{db_path}.pre-migration-*.sqlite.bak"), "backup must exist even when migration aborts"
