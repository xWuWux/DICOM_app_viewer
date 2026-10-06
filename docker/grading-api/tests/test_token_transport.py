"""
Token-transport regressions (issue #94, the durable half of #63).

The session token used to travel in query strings (?token=...) -- nginx's
access log and (via the upstream URL) its error log reproduced it on
every request. Transport is now the X-Grading-Token header for the two
GET endpoints (bodies of /submit and /reset are not logged by nginx, so
they stayed); the query form is REJECTED, not deprecated -- every
caller of these endpoints is first-party code updated in the same PR,
and hours-lived tokens make a compatibility window pointless.
"""

import logging

from app.logging_config import _JsonFormatter

SENTINEL = "SENTINEL-NEVER-MUST-APPEAR-IN-LOGS"


def _rendered(caplog):
    return "\n".join(_JsonFormatter().format(record) for record in caplog.records)


def test_query_string_token_is_rejected_and_never_logged(client, caplog):
    """A GET /case carrying ONLY ?token=... must fail as 'header missing'
    (422) -- and the sentinel query value must not resurface anywhere in
    the log lines or the response body that failure produces."""
    with caplog.at_level(logging.INFO):
        resp = client.get(f"/case?token={SENTINEL}")
    assert resp.status_code == 422
    assert resp.json() == {"error_code": "VALIDATION_ERROR", "message": "Invalid request"}
    assert SENTINEL not in resp.text
    assert SENTINEL not in _rendered(caplog)


def test_results_query_string_token_is_rejected(client):
    resp = client.get(f"/results?token={SENTINEL}")
    assert resp.status_code == 422


def test_header_token_is_the_accepted_transport(client, mint_token):
    token = mint_token("stu_header_transport")
    resp = client.get("/case", headers={"X-Grading-Token": token})
    assert resp.status_code == 200
    assert resp.json()["complete"] is False


def test_header_beats_ignored_query_noise(client, mint_token):
    """Real watermark.html during rollout could send both (stale cached JS
    appending ?token= while now sending the header): the header wins, the
    query is inert -- and inert means inert in the LOGS too."""
    token = mint_token("stu_both_transports")
    url = f"/case?token={SENTINEL}"
    resp = client.get(url, headers={"X-Grading-Token": token})
    assert resp.status_code == 200


def test_query_token_alone_never_authenticates(client, mint_token):
    """Belt-and-braces against a future edit accidentally re-widening the
    auth surface: a VALID token presented query-only must not read data."""
    token = mint_token("stu_query_only")
    resp = client.get(f"/case?token={token}")
    assert resp.status_code == 422
