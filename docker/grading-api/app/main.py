"""
Lung-RADS grading mechanics: 3-stage state machine (nauka/ocena/test).

See /docs/PROXMOX_DEPLOYMENT.md's sibling doc (README.md, "Lung-RADS grading")
for the overall design. Key rule this file enforces: ground_truth_category
and reference_report are NEVER returned by GET /case (only after POST
/submit, for the stage that's supposed to reveal them) -- the whole point of
the 3 stages is what gets revealed and when.
"""
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db

app = FastAPI(title="IP_CMC Grading API")


@app.on_event("startup")
def _startup():
    db.init_db()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _get_or_create_progress(conn, student_id: str):
    row = conn.execute(
        "SELECT * FROM progress WHERE student_id = ?", (student_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO progress (student_id, stage, case_order_index) VALUES (?, 'learning', 0)",
            (student_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM progress WHERE student_id = ?", (student_id,)
        ).fetchone()
    return row


def _get_case(conn, stage: str, order_index: int):
    return conn.execute(
        "SELECT * FROM cases WHERE stage = ? AND order_index = ?", (stage, order_index)
    ).fetchone()


def _advance_progress(conn, student_id: str, stage: str, order_index: int):
    """Move to the next case in this stage, or the next stage, or 'complete'."""
    next_case = _get_case(conn, stage, order_index + 1)
    if next_case is not None:
        conn.execute(
            "UPDATE progress SET case_order_index = ? WHERE student_id = ?",
            (order_index + 1, student_id),
        )
    else:
        stage_idx = db.STAGES.index(stage)
        if stage_idx + 1 < len(db.STAGES):
            next_stage = db.STAGES[stage_idx + 1]
            conn.execute(
                "UPDATE progress SET stage = ?, case_order_index = 0 WHERE student_id = ?",
                (next_stage, student_id),
            )
        else:
            conn.execute(
                "UPDATE progress SET stage = 'complete', case_order_index = 0 WHERE student_id = ?",
                (student_id,),
            )
    conn.commit()


@app.get("/case")
def get_case(student_id: str):
    conn = db.get_connection()
    try:
        progress = _get_or_create_progress(conn, student_id)
        if progress["stage"] == "complete":
            return {"complete": True}

        case = _get_case(conn, progress["stage"], progress["case_order_index"])
        if case is None:
            raise HTTPException(500, f"No case at stage={progress['stage']} index={progress['case_order_index']}")

        total_in_stage = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE stage = ?", (progress["stage"],)
        ).fetchone()[0]

        response = {
            "complete": False,
            "case_id": case["id"],
            "stage": case["stage"],
            "title": case["title"],
            "orthanc_study_uid": case["orthanc_study_uid"],
            "position": progress["case_order_index"] + 1,
            "total_in_stage": total_in_stage,
        }
        if case["stage"] in ("assessment", "test"):
            response["category_options"] = db.CATEGORY_LABELS
        return response
    finally:
        conn.close()


class SubmitBody(BaseModel):
    student_id: str
    case_id: int
    stage: str
    text: Optional[str] = None
    category: Optional[str] = None
    modifier_s: Optional[bool] = None
    time_spent_seconds: Optional[float] = None


@app.post("/submit")
def submit(body: SubmitBody):
    conn = db.get_connection()
    try:
        progress = _get_or_create_progress(conn, body.student_id)
        if progress["stage"] != body.stage:
            raise HTTPException(409, f"Submission stage '{body.stage}' doesn't match current progress stage '{progress['stage']}'")

        case = conn.execute("SELECT * FROM cases WHERE id = ?", (body.case_id,)).fetchone()
        if case is None or case["stage"] != body.stage:
            raise HTTPException(400, "case_id doesn't match the submitted stage")

        # Correctness is category-only: the modifier is recorded for later
        # analysis but doesn't affect scoring -- matches Dokumentacja/'s
        # framing of categorical comparison as the primary metric.
        is_correct = None
        if body.stage in ("assessment", "test"):
            is_correct = 1 if body.category == case["ground_truth_category"] else 0

        conn.execute(
            """INSERT INTO submissions
               (student_id, case_id, stage, submitted_category, submitted_modifier_s,
                submitted_text, is_correct, time_spent_seconds, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                body.student_id, body.case_id, body.stage, body.category,
                None if body.modifier_s is None else int(body.modifier_s),
                body.text, is_correct, body.time_spent_seconds, db.now(),
            ),
        )
        conn.commit()

        _advance_progress(conn, body.student_id, progress["stage"], progress["case_order_index"])

        if body.stage == "learning":
            return {"reference_report": case["reference_report"]}
        elif body.stage == "assessment":
            return {
                "correct": bool(is_correct),
                "ground_truth_category": case["ground_truth_category"],
                "ground_truth_modifier_s": bool(case["ground_truth_modifier_s"]),
            }
        else:  # test: no reveal
            return {}
    finally:
        conn.close()


class ResetBody(BaseModel):
    student_id: str


@app.post("/reset")
def reset(body: ResetBody):
    """Start over from the beginning. There's no separate 'main screen' in
    this single-page kiosk app (viewer + grading panel) -- once a student
    reaches 'complete', this is the way back to a fresh learning-stage case.
    Clears past submissions too (not just progress), so a repeat run
    doesn't leave stale rows alongside the new ones and skew /results."""
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM progress WHERE student_id = ?", (body.student_id,))
        conn.execute("DELETE FROM submissions WHERE student_id = ?", (body.student_id,))
        conn.commit()
        return {"reset": True}
    finally:
        conn.close()


@app.get("/results")
def results(student_id: str):
    conn = db.get_connection()
    try:
        progress = _get_or_create_progress(conn, student_id)
        if progress["stage"] != "complete":
            return {"complete": False}

        rows = conn.execute(
            """SELECT s.is_correct, c.ground_truth_category, s.submitted_category
               FROM submissions s JOIN cases c ON c.id = s.case_id
               WHERE s.student_id = ? AND s.stage = 'test'""",
            (student_id,),
        ).fetchall()

        total = len(rows)
        correct = sum(1 for r in rows if r["is_correct"])
        breakdown = [
            {"ground_truth": r["ground_truth_category"], "submitted": r["submitted_category"], "correct": bool(r["is_correct"])}
            for r in rows
        ]
        return {
            "complete": True,
            "test_total": total,
            "test_correct": correct,
            "accuracy": (correct / total) if total else None,
            "breakdown": breakdown,
        }
    finally:
        conn.close()
