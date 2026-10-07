"""
Input-validation + XSS-surface regressions (issue #99).

Covers:
  (a) SessionBody ID patterns/lengths: real-world IDs pass, markup/
      whitespace/oversized are 422;
  (b) CR #109 nit 3: pydantic's rejection message never embeds the
      rejected value -- the 422 log sanitizer truncates msg to 200 chars,
      but an embedded ID would still leak a truncated fragment;
  (c) stage/category are closed vocabularies (Literal), rejected at the
      schema boundary before any DB state is consulted;
  (d) the Literals never drift from db.STAGES / db.CATEGORY_LABELS;
  (e) the SQLite fresh-install CHECKs reject out-of-vocabulary rows that
      bypass the API (manual seeding).
"""

import logging
import os
import sqlite3
from typing import get_args

import pytest

from app import db as db_module
from app import main as main_module
from app.logging_config import _JsonFormatter

SENTINEL = "<img src=x onerror=alert-SENTINELVALUE99>"


def _mint_headers():
    return {"X-Coordinator-Key": os.environ["GRADING_COORDINATOR_KEY"]}


# ---- (a) ID shapes ----


def test_real_world_student_ids_are_accepted(client):
    for student_id in ("STU_LOCAL_TEST", "stu_old", "j.nowak@cmc.example", "a123456789"):
        resp = client.post(
            "/session",
            json={"student_id": student_id, "session_id": "sess_2026-10-07.1"},
            headers=_mint_headers(),
        )
        assert resp.status_code == 200, (student_id, resp.text)


def test_markup_whitespace_and_oversized_ids_are_rejected_without_echo(client, caplog):
    bad_ids = [
        SENTINEL,  # the XSS payload from the issue criteria
        "student id with spaces",
        "short\nnewline",
        "x" * 129,  # over the 128-char bound
        " leading-space",
    ]
    with caplog.at_level(logging.INFO):
        for bad in bad_ids:
            resp = client.post(
                "/session",
                json={"student_id": bad, "session_id": "sess_ok"},
                headers=_mint_headers(),
            )
            assert resp.status_code == 422, bad
            # response body: generic, sanitized (issue #93 contract holds)
            assert SENTINEL not in resp.text


def test_rejection_message_never_embeds_the_rejected_value(client, caplog):
    """CR #109 nit 3: msg truncation is not a containment strategy for
    values -- a rejected student_id must not appear in ANY log line,
    even truncated."""
    sentinel = "SENTINEL-ID-9F3A-<img>"
    with caplog.at_level(logging.INFO):
        client.post(
            "/session",
            json={"student_id": sentinel, "session_id": "sess_ok"},
            headers=_mint_headers(),
        )
    rendered = "\n".join(_JsonFormatter().format(r) for r in caplog.records)
    assert sentinel not in rendered
    # and it WAS a real 422 path (non-vacuous: the validation error logged)
    assert "validation_error" in rendered
    assert "string_pattern_mismatch" in rendered


# ---- (c) closed vocabularies at the schema boundary ----


def _token_for(client, student_id):
    resp = client.post(
        "/session",
        json={"student_id": student_id, "session_id": "s"},
        headers=_mint_headers(),
    )
    return resp.json()["token"]


def test_submit_rejects_unknown_stage_and_category_before_any_state(client):
    token = _token_for(client, "stu_vocab")
    headers = {"X-Grading-Token": token}
    resp = client.post(
        "/submit",
        json={"token": token, "case_id": 1, "stage": "hackme"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "SENTINEL" not in resp.text  # payload not echoed by name
    resp = client.post(
        "/submit",
        json={"token": token, "case_id": 1, "stage": "test", "category": "9Z"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_stage_mismatch_409_still_works_for_valid_literal_stages(client):
    """422-on-garbage did NOT swallow the semantic 409: a real stage value
    that merely mismatches progress still answers 409 (issue #61 path)."""
    token = _token_for(client, "stu_409")
    case = client.get("/case", headers={"X-Grading-Token": token}).json()
    wrong_stage = "test" if case["stage"] != "test" else "assessment"
    resp = client.post(
        "/submit",
        json={"token": token, "case_id": case["case_id"], "stage": wrong_stage},
    )
    assert resp.status_code == 409


# ---- (d) Literal/db sync ----


def test_literals_match_the_db_vocabulary():
    stage_field = main_module.SubmitBody.model_fields["stage"]
    assert set(get_args(stage_field.annotation)) == set(db_module.STAGES)
    cat_field = main_module.SubmitBody.model_fields["category"]
    # Optional[Literal[...]] -> union wrapping the literal
    literal_args = set()
    for arg in get_args(cat_field.annotation):
        literal_args.update(get_args(arg) or [arg])
    literal_args.discard(type(None))
    assert literal_args == {value for value, _label in db_module.CATEGORY_LABELS}


# ---- (e) fresh-install CHECK constraints ----


def test_sqlite_checks_reject_api_bypassing_writes(client):
    # the `client` fixture ran init_db -> CHECK-bearing fresh schema
    conn = sqlite3.connect(db_module.DB_PATH)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cases (stage, order_index, orthanc_study_uid, title,"
                " ground_truth_category, reference_report)"
                " VALUES ('learning', 99, '1.2.8', 't', '7Z', 'r')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cases (stage, order_index, orthanc_study_uid, title,"
                " ground_truth_category, reference_report)"
                " VALUES ('hackme', 99, '1.2.8', 't', '1', 'r')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO submissions (student_id, case_id, stage, submitted_at) VALUES ('x', 1, 'hackme', 1.0)"
            )
        # NULL-able vocabulary columns accept NULL (learning submissions)
        conn.execute(
            "INSERT INTO submissions (student_id, case_id, stage, submitted_at) VALUES ('x', 1, 'learning', 1.0)"
        )
    finally:
        conn.close()
