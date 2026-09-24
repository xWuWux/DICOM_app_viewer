"""
SQLite schema + seed data for the Lung-RADS grading mechanics.

Seed data is PLACEHOLDER content: none of the three sample studies
(CT_small/MR_small/BRAINIX -- see /README.md and scripts/load-sample-studies.sh)
are actually lung CTs. This is here to prove the 3-stage mechanics work end
to end, not to be clinically meaningful. Real curated content (500 studies,
real ground truth, real reference reports from a radiologist) is separate
work, not yet started.
"""

import os
import sqlite3
import time

DB_PATH = os.environ.get("GRADING_DB_PATH", "/data/grading.db")

# How long a minted session token stays valid. A single training/exam
# session is expected to last at most a few hours; 8h gives real headroom
# without tokens living forever. Configurable since real cohorts may need
# a different window.
TOKEN_TTL_SECONDS = int(os.environ.get("GRADING_TOKEN_TTL_SECONDS", 8 * 60 * 60))

STAGES = ["learning", "assessment", "test"]

# Single source of truth for category labels -- both the API responses and
# (indirectly, since the frontend just renders what /case returns) the UI
# pull from here, so the Polish text from the PM's spec lives in exactly
# one place.
CATEGORY_LABELS = [
    ("0", "Kategoria 0 – badanie niekompletne / wymagane porównanie z badaniami poprzednimi"),
    ("1", "Kategoria 1 – wynik negatywny, brak guzków lub zmiany jednoznacznie łagodne"),
    ("2", "Kategoria 2 – zmiany łagodne, bardzo niskie ryzyko złośliwości"),
    ("3", "Kategoria 3 – zmiany prawdopodobnie łagodne, niskie ryzyko, zalecana kontrola krótkoterminowa"),
    ("4A", "Kategoria 4A – podejrzane, umiarkowane ryzyko złośliwości"),
    ("4B", "Kategoria 4B – wysoce podejrzane, wysokie ryzyko złośliwości"),
    ("4X", "Kategoria 4X – kategoria 3/4 z dodatkowymi cechami zwiększającymi podejrzenie złośliwości"),
]

SEED_CASES = [
    # (stage, order_index, orthanc_study_uid, title, ground_truth_category, ground_truth_modifier_s, reference_report)
    (
        "learning",
        0,
        "1.3.6.1.4.1.5962.1.2.1.20040119072730.12322",  # CT_small
        "Przypadek 1 (nauka)",
        "2",
        0,
        "PLACEHOLDER — nie jest to prawdziwy opis kliniczny. "
        "Badanie TK klatki piersiowej bez cech guzków podejrzanych. "
        "Widoczna drobna zmiana łagodna o niskim ryzyku złośliwości. "
        "Kategoria referencyjna: Lung-RADS 2.",
    ),
    (
        "assessment",
        0,
        "1.3.6.1.4.1.5962.1.2.4.20040826185059.5457",  # MR_small
        "Przypadek 1 (ocena)",
        "3",
        0,
        "PLACEHOLDER — nie jest to prawdziwy opis kliniczny. "
        "Zmiana prawdopodobnie łagodna, zalecana kontrola krótkoterminowa. "
        "Kategoria referencyjna: Lung-RADS 3.",
    ),
    (
        "test",
        0,
        "2.16.840.1.113669.632.20.1211.10000357775",  # BRAINIX
        "Przypadek 1 (test)",
        "4A",
        1,
        "PLACEHOLDER — nie jest to prawdziwy opis kliniczny. "
        "Zmiana podejrzana, umiarkowane ryzyko złośliwości, obecna dodatkowo "
        "zmiana istotna klinicznie spoza płuc. Kategoria referencyjna: Lung-RADS 4A, modyfikator S.",
    ),
]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL mode (scaling concern raised when discussing a real multi-VM
    # cohort deployment): SQLite's default rollback-journal mode blocks
    # ALL readers for the duration of a write -- under concurrent
    # submissions (many students finishing a case around the same
    # moment), that serializes requests that don't need to touch each
    # other's data at all. WAL allows readers to proceed concurrently
    # with a single in-progress writer, which is the actual contention
    # pattern here (many /case reads, occasional /submit writes) -- a
    # meaningfully higher concurrency ceiling for a 50-user pilot without
    # the bigger step of migrating off SQLite entirely (still the right
    # move before a real 300-user cohort, per CLAUDE.md/README).
    # `journal_mode=WAL` is a database-file-level setting, not per-
    # connection -- calling it here is idempotent (a no-op once already
    # set) and self-migrating: it takes effect on an existing pre-WAL
    # database file the same way, no separate migration step needed.
    # `synchronous=NORMAL` is WAL's own documented pairing -- WAL's
    # write-ahead log already provides the durability guarantee that
    # makes the stricter (and slower) FULL setting unnecessary.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _migrate_existing_schema(conn):
    """Handles a database file that already existed before a later schema
    change -- CREATE TABLE IF NOT EXISTS is a genuine no-op once a table
    already exists in ANY form, not a schema-reconciliation step. Found
    the hard way: rebuilding grading-api against a real, previously-
    running /data/grading.db (predating issues #28/#29) broke every
    /case and /submit call outright with "no such column:
    case_assigned_at" -- the column/constraint additions below had never
    actually been verified against an existing database, only fresh ones
    (every test uses an empty tmp_path file via conftest.py's `client`
    fixture, which never exercises this path at all).
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    if "progress" in tables:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(progress)").fetchall()}
        if "case_assigned_at" not in cols:
            # Existing in-progress students' real assignment moment is
            # unknowable in hindsight -- backfilling "now" understates
            # their actual time-on-task for whatever case they're
            # currently on, but that's a one-time, one-case measurement
            # gap for students who were mid-session during this upgrade,
            # not an ongoing correctness issue.
            conn.execute("ALTER TABLE progress ADD COLUMN case_assigned_at REAL")
            conn.execute(
                "UPDATE progress SET case_assigned_at = ? WHERE case_assigned_at IS NULL",
                (time.time(),),
            )
            conn.commit()

    if "submissions" in tables:
        has_unique = any(row[2] for row in conn.execute("PRAGMA index_list(submissions)").fetchall())
        if not has_unique:
            # SQLite can't ALTER TABLE to add a constraint -- rebuild the
            # table under the new schema instead (SQLite's own documented
            # pattern for this). Keeps only the latest row per
            # (student_id, case_id, stage) if any pre-constraint
            # duplicates exist, rather than failing the migration outright
            # on a UNIQUE violation partway through.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(
                """
                ALTER TABLE submissions RENAME TO submissions_old;
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
                INSERT INTO submissions
                    SELECT * FROM submissions_old
                    WHERE id IN (
                        SELECT MAX(id) FROM submissions_old
                        GROUP BY student_id, case_id, stage
                    );
                DROP TABLE submissions_old;
                """
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys=ON")


def init_db():
    conn = get_connection()
    _migrate_existing_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            orthanc_study_uid TEXT NOT NULL,
            title TEXT NOT NULL,
            ground_truth_category TEXT NOT NULL,
            ground_truth_modifier_s INTEGER NOT NULL DEFAULT 0,
            reference_report TEXT NOT NULL,
            UNIQUE(stage, order_index)
        );

        -- case_assigned_at (issue #29): stamped server-side the moment a
        -- case becomes the student's active one (fresh progress, or
        -- _advance_progress landing on the next case/stage) -- the only
        -- honest way to measure time-on-task. The client's own
        -- Date.now()-based elapsed time is trivially editable (devtools,
        -- or a scripted request) and this data feeds a scientific
        -- publication, so it's no longer trusted for that value at all;
        -- see main.py's submit(). Only takes effect here for a genuinely
        -- fresh database -- _migrate_existing_schema() above handles an
        -- existing one.
        CREATE TABLE IF NOT EXISTS progress (
            student_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            case_order_index INTEGER NOT NULL,
            case_assigned_at REAL NOT NULL
        );

        -- UNIQUE(student_id, case_id, stage) (issue #28): /submit's own
        -- stage check (progress["stage"] != body.stage) and its INSERT are
        -- two separate steps with no locking between them -- a double-
        -- click or a scripted rapid-fire request could pass the check
        -- twice before either write lands, creating duplicate submissions
        -- for the same case/stage and skewing /results accuracy numbers.
        -- This constraint makes that a clean, guaranteed-consistent
        -- IntegrityError (mapped to 409 in main.py's submit()) instead of
        -- silently succeeding twice, without needing a manual transaction/
        -- row-lock around the whole read-then-write span.
        --
        -- Only takes effect here for a genuinely fresh database --
        -- _migrate_existing_schema() above rebuilds an existing
        -- submissions table under this same schema instead (SQLite can't
        -- ALTER TABLE to add a constraint to an existing table).
        CREATE TABLE IF NOT EXISTS submissions (
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

        -- Binds an unguessable, server-issued token to a student_id --
        -- the actual authorization mechanism (see main.py's /session and
        -- _resolve_token()). student_id/session_id themselves are never
        -- trusted as credentials from here on; they're only ever used for
        -- display (the watermark text), because ANY caller could set them
        -- to anything. A student_id can have at most one live token at a
        -- time (see POST /session's DELETE-then-INSERT) -- minting a new
        -- one revokes whatever came before it for that same student.
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );

        -- Indexes for performance (issue #1: missing indexes)
        CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id);
        CREATE INDEX IF NOT EXISTS idx_submissions_stage ON submissions(stage);
        CREATE INDEX IF NOT EXISTS idx_submissions_submitted ON submissions(submitted_at);
        CREATE INDEX IF NOT EXISTS idx_cases_stage ON cases(stage);
        CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
        """
    )
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    if count == 0:
        conn.executemany(
            """INSERT INTO cases
               (stage, order_index, orthanc_study_uid, title,
                ground_truth_category, ground_truth_modifier_s, reference_report)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            SEED_CASES,
        )
        conn.commit()
    conn.close()


def now() -> float:
    return time.time()
