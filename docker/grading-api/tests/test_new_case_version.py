"""scripts/new-case-version.py (issue #96): the safe, only legal way to change a ground truth
after students answered. Dry-run default, backup before write, old results untouched."""

import sqlite3
import subprocess
import sys
from pathlib import Path

from app import db as db_module

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "new-case-version.py"


def _run(db_path, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--stage",
            "test",
            "--order",
            "0",
            "--reason",
            "unit test",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _versions():
    c = db_module.get_connection()
    try:
        return [
            tuple(r)
            for r in c.execute("SELECT version, ground_truth_category FROM cases WHERE stage='test' ORDER BY version")
        ]
    finally:
        c.close()


def test_dry_run_writes_nothing(client):
    proc = _run(db_module.DB_PATH, "--category", "4B")
    assert proc.returncode == 0, proc.stderr
    assert "DRY-RUN" in proc.stdout
    assert _versions() == [(1, "4A")]


def test_apply_creates_next_version_with_backup_and_keeps_old_results(client):
    conn = db_module.get_connection()
    case_id = conn.execute("SELECT id FROM cases WHERE stage='test'").fetchone()[0]
    conn.execute(
        "INSERT INTO submissions (student_id, case_id, stage, submitted_category, is_correct, submitted_at,"
        " ground_truth_category, ground_truth_modifier_s, case_version)"
        " VALUES ('stu_ncv',?,'test','4A',1,1.0,'4A',1,1)",
        (case_id,),
    )
    conn.commit()
    conn.close()

    proc = _run(db_module.DB_PATH, "--category", "4B", "--apply")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "APPLIED" in proc.stdout
    assert _versions() == [(1, "4A"), (2, "4B")]
    assert list(Path(db_module.DB_PATH).parent.glob("*.pre-case-version-*.sqlite.bak"))

    c = sqlite3.connect(db_module.DB_PATH)
    frozen = c.execute(
        "SELECT ground_truth_category, case_version FROM submissions WHERE student_id='stu_ncv'"
    ).fetchone()
    c.close()
    assert frozen == ("4A", 1)


def test_rejects_invalid_category_unknown_position_and_noop(client):
    assert _run(db_module.DB_PATH, "--category", "9Z").returncode != 0
    bad_pos = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_module.DB_PATH),
            "--stage",
            "test",
            "--order",
            "99",
            "--category",
            "4B",
            "--reason",
            "x",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bad_pos.returncode != 0
    assert _run(db_module.DB_PATH, "--category", "4A").returncode != 0  # equals current: nothing to change


def test_refuses_to_create_a_database(tmp_path):
    proc = _run(tmp_path / "does-not-exist.db", "--category", "4B", "--apply")
    assert proc.returncode != 0
    assert not (tmp_path / "does-not-exist.db").exists()
