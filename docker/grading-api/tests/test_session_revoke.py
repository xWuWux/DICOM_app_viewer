"""
POST /session/revoke (issue #104).

The coordinator used to have exactly one way to make a token it had already
minted stop working: mint another one for the same student_id and rely on POST
/session's DELETE-first behavior. That is unusable as compensation for a
half-created session (Kasm call failed after the mint), and unusable when the
mint response was never parsed so the token value is not even known.

Covers:
  (a) revoking by token works and the token stops authenticating everywhere;
  (b) revoking by student_id works, including for a token the caller never
      saw -- and only that student's row goes away;
  (c) coordinator-key gate: missing and wrong key are 401 AUTH_*, never a
      silent no-op;
  (d) an unknown/already-gone token answers 200 revoked=false, not 404 -- this
      is a compensation step, its caller must not need to tell "already
      revoked" apart from "never existed";
  (e) exactly one selector is required;
  (f) the token value never appears in any response body or in the log line
      for a rejected request (issue #93's leak class, applied to the new
      endpoint that has a token in its payload by design).
"""

import logging
import os

from app import db as db_module
from app.logging_config import _JsonFormatter


def _coord_headers():
    return {"X-Coordinator-Key": os.environ["GRADING_COORDINATOR_KEY"]}


def _revoke(client, body, headers=None):
    return client.post("/session/revoke", json=body, headers=headers if headers is not None else _coord_headers())


# ---- (a) revoke by token ----


def test_revoke_by_token_invalidates_it_everywhere(client, mint_token):
    token = mint_token("STU_REVOKE_1")
    assert _revoke(client, {"token": token}).json()["revoked"] is True

    resp = client.get("/case", headers={"X-Grading-Token": token})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTH_INVALID_TOKEN"


def test_revoke_actually_deletes_the_row(client, mint_token):
    """revoked=true must mean the row is gone, not "we flagged it" -- a token
    that survives in the table is still a live credential."""
    token = mint_token("STU_REVOKE_2")
    _revoke(client, {"token": token})
    conn = db_module.get_connection()
    try:
        row = conn.execute("SELECT 1 FROM sessions WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()
    assert row is None


# ---- (b) revoke by student_id ----


def test_revoke_by_student_id_covers_a_token_the_caller_never_held(client, mint_token):
    """The escape hatch for the exact failure this endpoint exists for: the
    mint happened, its response was never parsed, nobody holds the value."""
    token = mint_token("STU_REVOKE_3")
    resp = _revoke(client, {"student_id": "STU_REVOKE_3"})
    assert resp.status_code == 200 and resp.json()["revoked"] is True
    assert client.get("/case", headers={"X-Grading-Token": token}).status_code == 401


def test_revoke_by_student_id_leaves_other_students_alone(client, mint_token):
    mine = mint_token("STU_REVOKE_4")
    theirs = mint_token("STU_OTHER_4")
    assert _revoke(client, {"student_id": "STU_REVOKE_4"}).json()["count"] == 1
    assert client.get("/case", headers={"X-Grading-Token": theirs}).status_code == 200
    assert client.get("/case", headers={"X-Grading-Token": mine}).status_code == 401


def test_revoke_by_student_id_only_touches_the_latest_token(client, mint_token):
    """POST /session keeps at most one live token per student_id, so count==1
    is the invariant here, not a coincidence of this test's data."""
    mint_token("STU_REVOKE_5")
    newest = mint_token("STU_REVOKE_5")
    assert _revoke(client, {"student_id": "STU_REVOKE_5"}).json()["count"] == 1
    assert client.get("/case", headers={"X-Grading-Token": newest}).status_code == 401


# ---- (c) the gate ----


def test_revoke_without_a_coordinator_key_is_401(client, mint_token):
    token = mint_token("STU_REVOKE_6")
    resp = client.post("/session/revoke", json={"token": token})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTH_INVALID_COORDINATOR_KEY"
    # and the token survives: a rejected revoke must never have side effects
    assert client.get("/case", headers={"X-Grading-Token": token}).status_code == 200


def test_revoke_with_a_wrong_coordinator_key_is_401(client, mint_token):
    token = mint_token("STU_REVOKE_7")
    resp = _revoke(client, {"token": token}, headers={"X-Coordinator-Key": "wrong-" + "k" * 40})
    assert resp.status_code == 401
    assert client.get("/case", headers={"X-Grading-Token": token}).status_code == 200


# ---- (d) idempotence ----


def test_revoking_an_unknown_token_is_200_revoked_false(client):
    """Not 404: see the module docstring (d). A 404 here would push callers
    into treating "someone got there first" as a new failure to handle."""
    resp = _revoke(client, {"token": "never-" + "minted" + "-0123456789abcdef0123456789abcdef"})
    assert resp.status_code == 200 and resp.json() == {"revoked": False, "count": 0}


def test_double_revoke_is_harmless(client, mint_token):
    token = mint_token("STU_REVOKE_8")
    assert _revoke(client, {"token": token}).json()["revoked"] is True
    assert _revoke(client, {"token": token}).json()["revoked"] is False


# ---- (e) selector shape ----


def test_selector_is_required_and_unambiguous(client, mint_token):
    token = mint_token("STU_REVOKE_9")
    neither = _revoke(client, {})
    both = _revoke(client, {"token": token, "student_id": "STU_REVOKE_9"})
    oversize = _revoke(client, {"token": "t" * 200})
    bad_student = _revoke(client, {"student_id": "<img src=x>"})
    for resp in (neither, both, oversize, bad_student):
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "VALIDATION_REVOKE_SELECTOR"
    # every one of those rejections left the token alive
    assert client.get("/case", headers={"X-Grading-Token": token}).status_code == 200


# ---- (f) the new endpoint must not become a leak ----


def test_rejections_never_echo_the_token(client, mint_token, caplog):
    """A revoke payload's whole content is a live credential. If a rejection
    echoed it -- in the body or the log line -- this endpoint would leak
    exactly what it exists to revoke (issue #93's class, see errors.py)."""
    token = mint_token("STU_REVOKE_10")
    with caplog.at_level(logging.WARNING):
        resp = _revoke(client, {"token": token, "student_id": "STU_REVOKE_10"})
    assert resp.status_code == 400
    assert token not in resp.text
    rendered = "".join(_JsonFormatter().format(record) for record in caplog.records)
    assert token not in rendered


def test_oversized_selector_rejected_without_reading_its_content(client):
    """Bound applies before the DELETE, and the message names only the field
    -- never the 200 characters that tripped it."""
    filler = "z" * 200
    resp = _revoke(client, {"token": filler})
    assert resp.status_code == 400
    assert filler not in resp.text
