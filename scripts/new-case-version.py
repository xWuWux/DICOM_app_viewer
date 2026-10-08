#!/usr/bin/env python3
"""Author a NEW VERSION of a case (issue #96): the only legal way to change a case's
ground truth once students have answered it.

An answered case's ground truth is frozen (DB trigger `cases_ground_truth_frozen`);
doctors who already answered keep their frozen snapshot and /results. New doctors get
the new version (`_get_case` serves the highest version at a position).

Dry-run by default: prints what WOULD change and writes nothing. Add --apply to write.
Applying makes a consistent pre-change copy first (VACUUM INTO) and does the INSERT in
one transaction. Grading-affecting change: --reason is mandatory and is logged.

Example:
    python3 scripts/new-case-version.py --db /data/grading.db --stage test --order 0 \\
        --category 4B --reason "radiologist re-read, ticket #123"            # dry-run
    python3 scripts/new-case-version.py ... --apply
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "docker" / "grading-api")
)
os.environ.setdefault("GRADING_COORDINATOR_KEY", "admin-script-only-key-" + "x" * 32)
from app import db  # noqa: E402  (constants only; DB path is passed explicitly)


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", required=True, help="path to grading.db")
    ap.add_argument("--stage", required=True, choices=db.STAGES)
    ap.add_argument(
        "--order", required=True, type=int, help="order_index of the case position"
    )
    ap.add_argument(
        "--category", help="new ground-truth category (default: keep current)"
    )
    ap.add_argument(
        "--modifier-s",
        type=int,
        choices=(0, 1),
        help="new S modifier (default: keep current)",
    )
    ap.add_argument("--title")
    ap.add_argument("--study-uid", help="new Orthanc StudyInstanceUID")
    ap.add_argument("--report", help="new reference report text")
    ap.add_argument(
        "--reason",
        required=True,
        help="why the key changes (logged; audit trail until #98)",
    )
    ap.add_argument(
        "--apply", action="store_true", help="write the change (default: dry-run)"
    )
    a = ap.parse_args()

    valid = {k for k, _ in db.CATEGORY_LABELS}
    if a.category is not None and a.category not in valid:
        return fail(f"category must be one of {sorted(valid)}")
    if not a.reason.strip():
        return fail("--reason must not be empty")
    if not os.path.isfile(a.db):
        return fail(f"{a.db} does not exist (refusing to create a database)")

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        if "version" not in {r[1] for r in conn.execute("PRAGMA table_info(cases)")}:
            return fail(
                "database is not on the versioned schema; start grading-api once (it migrates and backs up first)"
            )
        cur = conn.execute(
            "SELECT * FROM cases WHERE stage=? AND order_index=? ORDER BY version DESC LIMIT 1",
            (a.stage, a.order),
        ).fetchone()
        if cur is None:
            return fail(
                f"no case at stage={a.stage} order={a.order}; this tool only versions existing positions"
            )
        new = {
            "ground_truth_category": a.category
            if a.category is not None
            else cur["ground_truth_category"],
            "ground_truth_modifier_s": a.modifier_s
            if a.modifier_s is not None
            else cur["ground_truth_modifier_s"],
            "title": a.title if a.title is not None else cur["title"],
            "orthanc_study_uid": a.study_uid
            if a.study_uid is not None
            else cur["orthanc_study_uid"],
            "reference_report": a.report
            if a.report is not None
            else cur["reference_report"],
        }
        changed = {k: (cur[k], v) for k, v in new.items() if cur[k] != v}
        if not changed:
            return fail("nothing to change: every value equals the current version")
        answered = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE case_id=?", (cur["id"],)
        ).fetchone()[0]

        print(
            f"case: {a.stage}/{a.order}  current version {cur['version']} (id {cur['id']}) -> new version {cur['version'] + 1}"
        )
        for k, (old, val) in changed.items():
            shown_old, shown_val = (str(old)[:60], str(val)[:60])
            print(f"  {k}: {shown_old!r} -> {shown_val!r}")
        print(
            f"{answered} existing submission(s) on version {cur['version']} keep their frozen ground truth and /results."
        )
        print(f"reason: {a.reason}")

        if not a.apply:
            print("\nDRY-RUN: nothing written. Re-run with --apply to write.")
            return 0

        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = f"{a.db}.pre-case-version-{stamp}.sqlite.bak"
        conn.execute("VACUUM INTO ?", (backup,))
        os.chmod(backup, 0o600)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """INSERT INTO cases (stage, order_index, orthanc_study_uid, title, ground_truth_category,
                       ground_truth_modifier_s, reference_report, version) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    a.stage,
                    a.order,
                    new["orthanc_study_uid"],
                    new["title"],
                    new["ground_truth_category"],
                    new["ground_truth_modifier_s"],
                    new["reference_report"],
                    cur["version"] + 1,
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            return fail(f"write failed, database unchanged: {exc}")
        print(f"\nAPPLIED: version {cur['version'] + 1} created. Backup: {backup}")
        print(
            f"AUDIT {stamp} stage={a.stage} order={a.order} v{cur['version']}->v{cur['version'] + 1} reason={a.reason!r}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
