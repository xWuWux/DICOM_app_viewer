"""
Tests for structured logging (issue #72): every response carries a
request_id, every log line emitted while handling a request carries the
same one (the actual "trace/log correlation propagated across
boundaries" mechanism), auth failures and state transitions are logged,
and -- checked directly, not just implied by test_error_taxonomy.py's
client-side assertions -- secrets never end up in a log record.
"""

import json
import logging
import os

from app.logging_config import _JsonFormatter

# ---- The JSON formatter itself, in isolation ----


def _make_record(msg="hello", level=logging.INFO, extra=None, exc_info=None):
    record = logging.LogRecord(
        name="app.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_formatter_produces_valid_json_with_expected_keys():
    line = _JsonFormatter().format(_make_record())
    payload = json.loads(line)  # raises if this isn't a single valid JSON line
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload
    assert "request_id" in payload


def test_formatter_surfaces_extra_fields_as_top_level_keys():
    line = _JsonFormatter().format(_make_record(extra={"error_code": "AUTH_INVALID_TOKEN", "status_code": 401}))
    payload = json.loads(line)
    assert payload["error_code"] == "AUTH_INVALID_TOKEN"
    assert payload["status_code"] == 401


def test_formatter_includes_exception_info_when_present():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record(msg="unhandled_exception", level=logging.ERROR, exc_info=sys.exc_info())
    payload = json.loads(_JsonFormatter().format(record))
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


# ---- Request-ID correlation, through the real app ----


def test_response_always_carries_a_request_id(client):
    resp = client.get("/healthz")
    assert resp.headers.get("x-request-id")


def test_two_requests_get_different_request_ids(client):
    id_a = client.get("/healthz").headers["x-request-id"]
    id_b = client.get("/healthz").headers["x-request-id"]
    assert id_a != id_b


def test_a_supplied_request_id_is_honored(client):
    resp = client.get("/healthz", headers={"X-Request-Id": "test-fixed-id-123"})
    assert resp.headers["x-request-id"] == "test-fixed-id-123"


def test_access_log_line_carries_the_same_request_id_as_the_response(client, caplog):
    with caplog.at_level(logging.INFO):
        resp = client.get("/healthz", headers={"X-Request-Id": "correlate-me"})
    assert resp.headers["x-request-id"] == "correlate-me"
    access_records = [r for r in caplog.records if r.getMessage() == "request"]
    assert len(access_records) == 1
    assert access_records[0].request_id == "correlate-me"
    assert access_records[0].path == "/healthz"
    assert access_records[0].status_code == 200


# ---- Auth failures and state transitions are actually logged ----


def test_auth_failure_is_logged_with_its_error_code(client, caplog):
    with caplog.at_level(logging.WARNING):
        client.get("/case?token=not-a-real-token")
    app_error_records = [r for r in caplog.records if r.getMessage() == "app_error"]
    assert len(app_error_records) == 1
    assert app_error_records[0].error_code == "AUTH_INVALID_TOKEN"
    assert app_error_records[0].levelno == logging.WARNING


def test_stage_advance_is_logged(client, mint_token, caplog):
    token = mint_token("stu_log_stage_advance")
    case_id = client.get(f"/case?token={token}").json()["case_id"]
    with caplog.at_level(logging.INFO):
        client.post("/submit", json={"token": token, "case_id": case_id, "stage": "learning", "text": "x"})
    stage_records = [r for r in caplog.records if r.getMessage() == "stage_advanced"]
    assert len(stage_records) == 1
    assert stage_records[0].from_stage == "learning"
    assert stage_records[0].to_stage == "assessment"


def test_healthz_database_error_is_logged_with_full_traceback_server_side(client, monkeypatch, caplog):
    """The other half of test_error_taxonomy.py's regression test for
    issue #62: the exception the client must never see still needs to
    exist *somewhere* for real diagnosis -- confirm it's actually here,
    with a real traceback, not just silently swallowed."""
    import sqlite3

    from app import main as main_module

    sensitive_detail = "unable to open database file: /a/sensitive/internal/path/grading.db"

    def _boom():
        raise sqlite3.OperationalError(sensitive_detail)

    monkeypatch.setattr(main_module.db, "get_connection", _boom)

    with caplog.at_level(logging.ERROR):
        client.get("/healthz")

    error_records = [r for r in caplog.records if r.getMessage() == "healthz_database_error"]
    assert len(error_records) == 1
    assert error_records[0].exc_info is not None
    assert sensitive_detail in str(error_records[0].exc_info[1])


# ---- Secrets never end up in a log record ----


def test_no_log_record_ever_contains_the_coordinator_key_or_a_real_token(client, caplog):
    coordinator_key = os.environ["GRADING_COORDINATOR_KEY"]

    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/session",
            json={"student_id": "stu_secret_check", "session_id": "sess_secret_check"},
            headers={"X-Coordinator-Key": coordinator_key},
        )
        token = resp.json()["token"]
        client.get(f"/case?token={token}")
        client.post(
            "/submit",
            json={
                "token": token,
                "case_id": client.get(f"/case?token={token}").json()["case_id"],
                "stage": "learning",
                "text": "a student's free-text answer",
            },
        )

    for record in caplog.records:
        rendered = repr(record.__dict__)
        assert coordinator_key not in rendered
        assert token not in rendered
