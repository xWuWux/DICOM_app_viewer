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

from fastapi.testclient import TestClient

from app.logging_config import _JsonFormatter
from app.main import app

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


def test_unhandled_500_log_line_and_response_keep_the_request_id(client, mint_token, monkeypatch, caplog):
    """issue #95's regression test: the unhandled-exception handler lives
    on Starlette's outer ServerErrorMiddleware, outside the middleware
    that sets request_id -- before the fix, its log line (the single most
    important line to correlate) came out with request_id "-" and the
    500 response carried no X-Request-Id at all."""
    from app import main as main_module

    token = mint_token("stu_500_correlation")

    def _boom(conn, student_id):
        raise RuntimeError("unbuggable bug for issue #95")

    monkeypatch.setattr(main_module, "_get_or_create_progress", _boom)

    with TestClient(app, raise_server_exceptions=False) as non_raising_client:
        resp = non_raising_client.get(f"/case?token={token}", headers={"X-Request-Id": "correlate-my-500"})

    assert resp.status_code == 500
    assert resp.headers.get("x-request-id") == "correlate-my-500"

    error_records = [r for r in caplog.records if r.getMessage() == "unhandled_exception"]
    assert len(error_records) == 1
    assert error_records[0].request_id == "correlate-my-500"
    assert error_records[0].request_id == resp.headers["x-request-id"]


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


def test_422_never_logs_token_or_student_text(client, mint_token, caplog):
    """issue #93's regression test, both leak shapes at once:

    (a) POST /submit missing case_id -- pydantic's error `input` for a
        partially-valid body is the ENTIRE body, live session token
        included; (b) an overlong `text` -- the error for that one field
        still carried the whole ~20 KB answer in `input`.

    Asserted against the *rendered* JSON line (what actually lands in
    `docker logs`), not just the record's own attrs, and with a size
    bound so a 422 storm can't produce unbounded log growth."""
    from app.logging_config import _JsonFormatter

    token = mint_token("stu_422_leak")
    case_id = client.get(f"/case?token={token}").json()["case_id"]
    answer_marker = "STUDENT-ANSWER-" + "y" * 20_000

    with caplog.at_level(logging.INFO):
        # (a) valid token + stage + text, missing case_id -> 422 whose
        # exc.errors()[*]["input"] would be the whole body, token and all.
        client.post("/submit", json={"token": token, "stage": "learning", "text": answer_marker})
        # (b) well-formed body, overlong text -> 422 whose error `input`
        # is the full answer string.
        client.post("/submit", json={"token": token, "case_id": case_id, "stage": "learning", "text": answer_marker})

    validation_records = [r for r in caplog.records if r.getMessage() == "validation_error"]
    assert len(validation_records) == 2

    for record in validation_records:
        rendered = _JsonFormatter().format(record)
        assert token not in rendered
        assert "STUDENT-ANSWER-" not in rendered
        assert len(rendered) < 2_000

    # Diagnostics survive the sanitization: the operator can still see
    # WHICH field was rejected and why.
    errors = validation_records[0].errors
    assert any("case_id" in loc for err in errors for loc in [err.get("loc", [])])
    assert all("input" not in err and "ctx" not in err for err in errors)


def test_sanitizer_truncates_loc_elements():
    """CR #109 item 1: loc entries are field names, but nothing in this
    function's contract guarantees callers only ever pass known schema
    names (pydantic embeds value-internal paths for structured fields),
    so each element is bounded like msg is. Unit-level on purpose -- the
    current flat models never *emit* an oversized loc over HTTP
    (top-level unknown fields are ignored, not reported), and the
    sanitizer must not depend on that staying true."""
    from app.errors import _sanitized_validation_errors

    huge = "a" * 500
    (out,) = _sanitized_validation_errors([{"loc": ["body", huge], "type": "extra_forbidden", "msg": "m" * 500}])
    assert out["loc"] == ["body", "a" * 64]
    assert out["msg"] == "m" * 200


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
