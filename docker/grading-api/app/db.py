"""
SQLite schema + seed data for the Lung-RADS grading mechanics.

Seed data is PLACEHOLDER content: none of the three sample studies
(CT_small/MR_small/BRAINIX -- see /README.md and scripts/load-sample-studies.sh)
are actually lung CTs. This is here to prove the 3-stage mechanics work end
to end, not to be clinically meaningful. Real curated content (500 studies,
real ground truth, real reference reports from a radiologist) is separate
work tracked in Dokumentacja/, not this.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("GRADING_DB_PATH", "/data/grading.db")

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
        "learning", 0,
        "1.3.6.1.4.1.5962.1.2.1.20040119072730.12322",  # CT_small
        "Przypadek 1 (nauka)",
        "2", 0,
        "PLACEHOLDER — nie jest to prawdziwy opis kliniczny. "
        "Badanie TK klatki piersiowej bez cech guzków podejrzanych. "
        "Widoczna drobna zmiana łagodna o niskim ryzyku złośliwości. "
        "Kategoria referencyjna: Lung-RADS 2.",
    ),
    (
        "assessment", 0,
        "1.3.6.1.4.1.5962.1.2.4.20040826185059.5457",  # MR_small
        "Przypadek 1 (ocena)",
        "3", 0,
        "PLACEHOLDER — nie jest to prawdziwy opis kliniczny. "
        "Zmiana prawdopodobnie łagodna, zalecana kontrola krótkoterminowa. "
        "Kategoria referencyjna: Lung-RADS 3.",
    ),
    (
        "test", 0,
        "2.16.840.1.113669.632.20.1211.10000357775",  # BRAINIX
        "Przypadek 1 (test)",
        "4A", 1,
        "PLACEHOLDER — nie jest to prawdziwy opis kliniczny. "
        "Zmiana podejrzana, umiarkowane ryzyko złośliwości, obecna dodatkowo "
        "zmiana istotna klinicznie spoza płuc. Kategoria referencyjna: Lung-RADS 4A, modyfikator S.",
    ),
]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
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

        CREATE TABLE IF NOT EXISTS progress (
            student_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            case_order_index INTEGER NOT NULL
        );

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
            submitted_at REAL NOT NULL
        );
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
