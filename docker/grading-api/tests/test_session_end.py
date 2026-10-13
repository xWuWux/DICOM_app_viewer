"""POST /session/end: the doctor ends their own session after approving the review.

It revokes the token (access closed, so the window loop in the session closes Weasis) and keeps every
recorded answer. It is refused until the review was approved."""

import logging

from app import db as db_module
from app.logging_config import _JsonFormatter

H = "X-Grading-Token"


def _finish(client, token, approve=True):
    for body in ({"text": "x"}, {"category": "3", "modifier_s": False}, {"category": "4A", "modifier_s": True}):
        case = client.get("/case", headers={H: token}).json()
        client.post("/submit", json={"token": token, "case_id": case["case_id"], "stage": case["stage"], **body})
    if approve:
        assert client.post("/finalize", json={"token": token}).status_code == 200


def test_ending_after_approval_closes_access_and_keeps_every_answer(client, mint_token):
    token = mint_token("stu_end")
    _finish(client, token)
    resp = client.post("/session/end", json={"token": token})
    assert resp.status_code == 200 and resp.json() == {"ended": True}
    for call in (
        lambda: client.get("/case", headers={H: token}),
        lambda: client.get("/results", headers={H: token}),
        lambda: client.get("/review", headers={H: token}),
        lambda: client.post("/finalize", json={"token": token}),
    ):
        assert call().status_code == 401
    conn = db_module.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM submissions WHERE student_id = 'stu_end'").fetchone()[0] == 3
        row = conn.execute("SELECT stage, finalized_at FROM progress WHERE student_id = 'stu_end'").fetchone()
        assert row["stage"] == "complete" and row["finalized_at"] is not None
    finally:
        conn.close()


def test_it_is_refused_before_the_review_is_approved(client, mint_token):
    token = mint_token("stu_early")
    resp = client.post("/session/end", json={"token": token})
    assert resp.status_code == 409 and resp.json()["error_code"] == "SESSION_END_NOT_FINALIZED"
    _finish(client, token, approve=False)  # complete but not approved
    assert client.post("/session/end", json={"token": token}).status_code == 409
    assert client.get("/case", headers={H: token}).status_code == 200  # nothing was revoked


def test_a_second_call_and_a_bad_token_are_401(client, mint_token):
    token = mint_token("stu_twice")
    _finish(client, token)
    assert client.post("/session/end", json={"token": token}).status_code == 200
    assert client.post("/session/end", json={"token": token}).status_code == 401
    assert client.post("/session/end", json={"token": "nope"}).status_code == 401


def test_it_ends_only_the_callers_session(client, mint_token):
    a, b = mint_token("stu_a_end"), mint_token("stu_b_end")
    _finish(client, a)
    _finish(client, b)
    client.post("/session/end", json={"token": a})
    assert client.get("/results", headers={H: b}).status_code == 200


def test_the_token_never_appears_in_the_log(client, mint_token, caplog):
    token = mint_token("stu_log")
    _finish(client, token)
    with caplog.at_level(logging.INFO):
        client.post("/session/end", json={"token": token})
    assert token not in "\n".join(_JsonFormatter().format(r) for r in caplog.records)


def test_a_new_link_for_the_same_doctor_works_after_ending(client, mint_token):
    token = mint_token("stu_again")
    _finish(client, token)
    client.post("/session/end", json={"token": token})
    fresh = mint_token("stu_again", "sess2")
    assert client.get("/results", headers={H: fresh}).json()["finalized"] is True  # progress is still there
