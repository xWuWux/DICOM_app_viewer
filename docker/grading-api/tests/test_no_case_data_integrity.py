"""
Tests for the two server-side-only data-integrity branches that were the
only uncovered lines at the point the coverage gate (issue #101) was
introduced: main.py's `no_case_at_progress_position` handlers in GET
/case and POST /submit (progress claims a (stage, order_index) the seed
data doesn't cover -- a broken/partial cases table, not a client
mistake -> logged, generic 500, never the internal detail).
"""

import logging

from app import db as db_module


def _delete_case(student_stage: str, order_index: int) -> None:
    conn = db_module.get_connection()
    try:
        conn.execute("DELETE FROM cases WHERE stage = ? AND order_index = ?", (student_stage, order_index))
        conn.commit()
    finally:
        conn.close()


def test_case_endpoint_returns_generic_500_when_progress_points_at_missing_case(client, mint_token, caplog):
    token = mint_token("stu_missing_case_get")
    assert client.get(f"/case?token={token}").json()["complete"] is False
    _delete_case("learning", 0)  # the case progress is standing on

    with caplog.at_level(logging.ERROR):
        resp = client.get(f"/case?token={token}")

    assert resp.status_code == 500
    assert resp.json() == {"error_code": "SERVER_ERROR", "message": "Internal server error"}
    records = [r for r in caplog.records if r.getMessage() == "no_case_at_progress_position"]
    assert len(records) == 1
    assert records[0].stage == "learning"
    assert records[0].case_order_index == 0


def test_submit_endpoint_returns_generic_500_when_progress_points_at_missing_case(client, mint_token, caplog):
    token = mint_token("stu_missing_case_submit")
    # Create the progress row first (minting a token alone doesn't).
    client.get(f"/case?token={token}")
    # Park progress at an assessment position that has no seeded case
    # (the seed covers order_index 0 per stage only -- index 4 is empty
    # by construction, no DELETE needed; CR #112 item 2).
    conn = db_module.get_connection()
    try:
        conn.execute(
            "UPDATE progress SET stage = 'assessment', case_order_index = 4 WHERE student_id = ?",
            ("stu_missing_case_submit",),
        )
        conn.commit()
    finally:
        conn.close()

    with caplog.at_level(logging.ERROR):
        resp = client.post("/submit", json={"token": token, "case_id": 999, "stage": "assessment", "category": "3"})

    assert resp.status_code == 500
    assert resp.json() == {"error_code": "SERVER_ERROR", "message": "Internal server error"}
    records = [r for r in caplog.records if r.getMessage() == "no_case_at_progress_position"]
    assert len(records) == 1
    assert records[0].stage == "assessment"
    assert records[0].case_order_index == 4
