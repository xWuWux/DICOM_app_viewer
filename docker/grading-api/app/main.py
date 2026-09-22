"""
Lung-RADS grading mechanics: 3-stage state machine (nauka/ocena/test).

See /docs/PROXMOX_DEPLOYMENT.md's sibling doc (README.md, "Lung-RADS grading")
for the overall design. Key rule this file enforces: ground_truth_category
and reference_report are NEVER returned by GET /case (only after POST
/submit, for the stage that's supposed to reveal them) -- the whole point of
the 3 stages is what gets revealed and when.

Authorization (README.md flagged this as a real, unfixed gap; this closes
it): every endpoint below used to take student_id as a bare, client-
supplied parameter with nothing checking it actually belonged to the
caller -- any request could read or submit as any student_id. Now every
endpoint takes an unguessable `token` instead, minted once by POST /session
(coordinator-only, see GRADING_COORDINATOR_KEY below) and resolved
server-side to the real student_id via the `sessions` table. student_id/
session_id are never trusted as credentials again from here on -- they're
only ever used for display (the watermark text), which is fine since
displaying the wrong ID isn't a security problem, only trusting it for
grading actions was.
"""
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import db

app = FastAPI(title="IP_CMC Grading API")

# Shared secret only the coordinator (scripts/create-session.py, run by
# whoever mints links) knows -- required so POST /session can't just be
# called directly by a student's own browser to mint a token for anyone
# else's student_id, which would recreate the exact hole this closes.
# Fails loudly at import time if unset, same philosophy as
# docker-compose.yml's ORTHANC_PASSWORD -- never silently run with no
# secret configured.
COORDINATOR_KEY = os.environ["GRADING_COORDINATOR_KEY"]


@app.on_event("startup")
def _startup():
    db.init_db()


@app.get("/healthz")
def healthz():
    """Health check with database connection verification (issue #6)."""
    try:
        conn = db.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(503, f"Database error: {str(e)}")


class SessionBody(BaseModel):
    student_id: str
    session_id: str


@app.post("/session")
def create_session(body: SessionBody, x_coordinator_key: str = Header(...)):
    """Mint a fresh token bound to student_id, the only path that creates
    that binding. Called by scripts/create-session.py right after minting
    the Kasm session itself, not by anything running inside a session."""
    if not secrets.compare_digest(x_coordinator_key, COORDINATOR_KEY):
        raise HTTPException(401, "Invalid coordinator key")

    conn = db.get_connection()
    try:
        now = db.now()
        # Opportunistic cleanup, not a separate cron job -- cheap, and
        # keeps this table from growing unbounded across many sessions.
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        # A student_id has at most one live token: minting a new one
        # revokes whatever token existed before it for that same
        # student_id (e.g. a coordinator re-minting a link they suspect
        # leaked).
        conn.execute("DELETE FROM sessions WHERE student_id = ?", (body.student_id,))

        token = secrets.token_urlsafe(32)
        expires_at = now + db.TOKEN_TTL_SECONDS
        conn.execute(
            "INSERT INTO sessions (token, student_id, session_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token, body.student_id, body.session_id, now, expires_at),
        )
        conn.commit()
        return {"token": token, "expires_at": expires_at}
    finally:
        conn.close()


def _resolve_token(conn, token: str) -> str:
    """Every other endpoint's only path to a student_id -- never trust one
    handed in directly by the caller. Returns the student_id a valid,
    unexpired token was minted for; raises 401 otherwise."""
    row = conn.execute(
        "SELECT student_id, expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if row is None:
        raise HTTPException(401, "Invalid or unknown token")
    if row["expires_at"] < db.now():
        raise HTTPException(401, "Token expired")
    return row["student_id"]


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
def get_case(token: str):
    conn = db.get_connection()
    try:
        student_id = _resolve_token(conn, token)
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
    token: str
    case_id: int
    stage: str
    text: Optional[str] = None
    category: Optional[str] = None
    modifier_s: Optional[bool] = None
    time_spent_seconds: Optional[float] = None
    # (validation lives in the /submit handler below, not here -- a
    # same-named @staticmethod without a @validator/@field_validator
    # decorator was added alongside it in an earlier revision, but Pydantic
    # never calls a validation method that isn't actually registered as one;
    # it was dead code, removed rather than left as misleading no-op "validation")


@app.post("/submit")
def submit(body: SubmitBody):
    conn = db.get_connection()
    try:
        student_id = _resolve_token(conn, body.token)
        progress = _get_or_create_progress(conn, student_id)
        if progress["stage"] != body.stage:
            raise HTTPException(409, f"Submission stage '{body.stage}' doesn't match current progress stage '{progress['stage']}'")

        case = conn.execute("SELECT * FROM cases WHERE id = ?", (body.case_id,)).fetchone()
        if case is None or case["stage"] != body.stage:
            raise HTTPException(400, "case_id doesn't match the submitted stage")

        # Validate time_spent_seconds (issue #2: missing validation)
        if body.time_spent_seconds is not None:
            if body.time_spent_seconds < 0 or body.time_spent_seconds > 7200:
                raise HTTPException(400, "time_spent_seconds must be between 0 and 7200 seconds")

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
                student_id, body.case_id, body.stage, body.category,
                None if body.modifier_s is None else int(body.modifier_s),
                body.text, is_correct, body.time_spent_seconds, db.now(),
            ),
        )
        conn.commit()

        _advance_progress(conn, student_id, progress["stage"], progress["case_order_index"])

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
    token: str


@app.post("/reset")
def reset(body: ResetBody):
    """Start over from the beginning. There's no separate 'main screen' in
    this single-page kiosk app (viewer + grading panel) -- once a student
    reaches 'complete', this is the way back to a fresh learning-stage case.
    Clears past submissions too (not just progress), so a repeat run
    doesn't leave stale rows alongside the new ones and skew /results."""
    conn = db.get_connection()
    try:
        student_id = _resolve_token(conn, body.token)
        conn.execute("DELETE FROM progress WHERE student_id = ?", (student_id,))
        conn.execute("DELETE FROM submissions WHERE student_id = ?", (student_id,))
        conn.commit()
        return {"reset": True}
    finally:
        conn.close()


@app.get("/results")
def results(token: str):
    conn = db.get_connection()
    try:
        student_id = _resolve_token(conn, token)
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
