"""
Unit tests for grading-api's 3-stage Lung-RADS state machine (issue #8)
and its session-token authorization (README.md flagged the lack of it as
a real, serious gap: student_id used to be a bare, client-supplied
parameter nothing checked against the caller -- any request could act as
any student).

These exist to protect the pedagogically/security-critical invariants this
project keeps stating in prose (README.md, CLAUDE.md, main.py's own
docstring) but had never had automated, isolated coverage for:
  - a token is required everywhere, and only POST /session can mint one,
    itself gated by a coordinator-only secret;
  - an invalid, unknown, or expired token is rejected, not silently
    treated as some default identity;
  - minting a new token for a student_id revokes whatever token existed
    before it for that same student_id;
  - ground truth and reference reports are NEVER returned before the stage
    that's supposed to reveal them;
  - the test stage reveals nothing at all, unlike learning/assessment;
  - progress correctly advances case -> case, then stage -> stage, then
    to "complete";
  - two students' progress and submissions never leak into each other;
  - the input validation main.py already has (stage mismatches,
    time_spent_seconds range) actually behaves as documented.

Every test gets its own empty, isolated SQLite file via the `client`
fixture in conftest.py -- no shared state between tests, no dependency on
a real running stack (unlike scripts/smoke-test.sh, which is an
end-to-end check against the real docker-compose services).

Assertions below are pinned to the exact seed data in app/db.py's
SEED_CASES (one case per stage; learning's ground truth is category "2",
assessment's is "3", test's is "4A" with modifier_s=1) -- if that seed
data ever changes, the specific values asserted here need updating too,
not just the mechanics being tested.
"""
import os

from app import db as db_module


def _case_id(client, token):
    return client.get(f"/case?token={token}").json()["case_id"]


# ---- Session tokens: the actual authorization mechanism ----


def test_session_requires_the_coordinator_key(client):
    resp = client.post(
        "/session",
        json={"student_id": "stu_1", "session_id": "sess_1"},
        headers={"X-Coordinator-Key": "definitely-not-the-real-key"},
    )
    assert resp.status_code == 401


def test_session_with_no_coordinator_key_header_is_rejected(client):
    resp = client.post("/session", json={"student_id": "stu_1", "session_id": "sess_1"})
    # FastAPI's own required-header validation (422) fires before main.py's
    # code ever runs -- still a hard rejection either way, which is what
    # actually matters here.
    assert resp.status_code in (401, 422)


def test_case_rejects_an_unknown_token(client):
    resp = client.get("/case?token=this-token-was-never-minted")
    assert resp.status_code == 401


def test_case_rejects_an_expired_token(client, mint_token):
    token = mint_token("stu_2")
    conn = db_module.get_connection()
    try:
        # Simulate time passing rather than waiting out the real TTL --
        # directly age the row past its own expiry.
        conn.execute("UPDATE sessions SET expires_at = 0 WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()

    resp = client.get(f"/case?token={token}")
    assert resp.status_code == 401


def test_minting_a_new_token_revokes_the_previous_one_for_that_student(client, mint_token):
    old_token = mint_token("stu_3")
    assert client.get(f"/case?token={old_token}").status_code == 200

    new_token = mint_token("stu_3")
    assert new_token != old_token
    assert client.get(f"/case?token={old_token}").status_code == 401
    assert client.get(f"/case?token={new_token}").status_code == 200


def test_two_students_get_independent_tokens_and_state(client, mint_token):
    """The actual point of the whole token system: two different callers
    can never act as each other, even by guessing at student IDs -- there's
    no student_id parameter left anywhere for one to guess."""
    token_a = mint_token("stu_a")
    token_b = mint_token("stu_b")

    client.post(
        "/submit",
        json={"token": token_a, "case_id": _case_id(client, token_a), "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )

    assert client.get(f"/case?token={token_a}").json()["stage"] == "assessment"
    assert client.get(f"/case?token={token_b}").json()["stage"] == "learning"


# ---- The 3-stage state machine itself (all via a minted token) ----


def test_fresh_student_starts_at_learning_stage(client, mint_token):
    token = mint_token("stu_fresh")
    resp = client.get(f"/case?token={token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["complete"] is False
    assert data["stage"] == "learning"
    assert data["position"] == 1
    assert data["total_in_stage"] == 1
    # Learning stage has no structured category picker -- category_options
    # is only added for assessment/test (see main.py's get_case()).
    assert "category_options" not in data


def test_case_response_never_leaks_ground_truth_or_reference_report(client, mint_token):
    """The single most important invariant this whole feature exists to
    enforce, checked across all three stages generically (not just
    "learning doesn't leak") so a future field added to the /case response
    can't quietly reintroduce this."""
    token = mint_token("stu_leak_check")
    seen_stages = []
    while True:
        data = client.get(f"/case?token={token}").json()
        if data.get("complete"):
            break
        seen_stages.append(data["stage"])
        assert "ground_truth_category" not in data
        assert "ground_truth_modifier_s" not in data
        assert "reference_report" not in data

        body = {
            "token": token,
            "case_id": data["case_id"],
            "stage": data["stage"],
            "time_spent_seconds": 5,
        }
        if data["stage"] == "learning":
            body["text"] = "impression"
        else:
            body["category"] = "2"
            body["modifier_s"] = False
        client.post("/submit", json=body)

    assert seen_stages == ["learning", "assessment", "test"]


def test_learning_submit_reveals_reference_report_only(client, mint_token):
    token = mint_token("stu_2")
    case_id = _case_id(client, token)
    resp = client.post(
        "/submit",
        json={
            "token": token,
            "case_id": case_id,
            "stage": "learning",
            "text": "moja ocena",
            "time_spent_seconds": 30,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"reference_report"}
    assert "Lung-RADS 2" in data["reference_report"]


def test_learning_submit_advances_to_assessment_stage(client, mint_token):
    token = mint_token("stu_3")
    case_id = _case_id(client, token)
    client.post(
        "/submit",
        json={
            "token": token,
            "case_id": case_id,
            "stage": "learning",
            "text": "x",
            "time_spent_seconds": 1,
        },
    )
    data = client.get(f"/case?token={token}").json()
    assert data["stage"] == "assessment"
    assert data["position"] == 1
    assert data["category_options"]  # dropdown options present now


def test_assessment_submit_correct_category_reveals_ground_truth(client, mint_token):
    # Drive stu_4 through learning first (mechanics already covered above;
    # here we're only checking assessment's own reveal behavior).
    token = mint_token("stu_4")
    case_id = _case_id(client, token)
    client.post(
        "/submit",
        json={"token": token, "case_id": case_id, "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )
    case_id = _case_id(client, token)
    resp = client.post(
        "/submit",
        json={
            "token": token, "case_id": case_id, "stage": "assessment",
            "category": "3", "modifier_s": False, "time_spent_seconds": 10,
        },
    )
    data = resp.json()
    assert data["correct"] is True
    assert data["ground_truth_category"] == "3"
    assert data["ground_truth_modifier_s"] is False


def test_assessment_submit_incorrect_category_still_reveals_ground_truth(client, mint_token):
    """Assessment always reveals ground truth, correct or not -- only the
    test stage withholds it entirely. Getting this backwards (e.g. hiding
    ground truth on a wrong answer) would break the stage's whole
    cross-check purpose."""
    token = mint_token("stu_5")
    case_id = _case_id(client, token)
    client.post(
        "/submit",
        json={"token": token, "case_id": case_id, "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )
    case_id = _case_id(client, token)
    resp = client.post(
        "/submit",
        json={
            "token": token, "case_id": case_id, "stage": "assessment",
            "category": "1", "modifier_s": False, "time_spent_seconds": 10,
        },
    )
    data = resp.json()
    assert data["correct"] is False
    assert data["ground_truth_category"] == "3"


def test_test_stage_submit_reveals_nothing(client, mint_token):
    """The other core invariant: no correct/incorrect feedback, no ground
    truth, nothing -- an empty object, not just a missing key or two."""
    token = mint_token("stu_6")
    for stage in ("learning", "assessment"):
        case_id = _case_id(client, token)
        body = {"token": token, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = "3"
            body["modifier_s"] = False
        client.post("/submit", json=body)

    case_id = _case_id(client, token)
    resp = client.post(
        "/submit",
        json={
            "token": token, "case_id": case_id, "stage": "test",
            "category": "4A", "modifier_s": True, "time_spent_seconds": 10,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {}


def test_completing_all_stages_marks_complete(client, mint_token):
    token = mint_token("stu_7")
    for stage in ("learning", "assessment", "test"):
        case_id = _case_id(client, token)
        body = {"token": token, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = "3"
            body["modifier_s"] = False
        client.post("/submit", json=body)

    data = client.get(f"/case?token={token}").json()
    assert data == {"complete": True}


def test_results_before_completion(client, mint_token):
    token = mint_token("stu_8")
    resp = client.get(f"/results?token={token}")
    assert resp.json() == {"complete": False}


def test_results_after_completion_reports_accuracy(client, mint_token):
    token = mint_token("stu_9")
    for stage, category in (("learning", None), ("assessment", "3"), ("test", "4A")):
        case_id = _case_id(client, token)
        body = {"token": token, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = category  # both submitted correctly here
            body["modifier_s"] = stage == "test"
        client.post("/submit", json=body)

    data = client.get(f"/results?token={token}").json()
    assert data["complete"] is True
    assert data["test_total"] == 1
    assert data["test_correct"] == 1
    assert data["accuracy"] == 1.0
    assert data["breakdown"] == [
        {"ground_truth": "4A", "submitted": "4A", "correct": True}
    ]


def test_submit_stage_mismatch_returns_409(client, mint_token):
    token = mint_token("stu_10")
    case_id = _case_id(client, token)  # student is actually at "learning"
    resp = client.post(
        "/submit",
        json={
            "token": token, "case_id": case_id, "stage": "assessment",
            "category": "3", "modifier_s": False, "time_spent_seconds": 1,
        },
    )
    assert resp.status_code == 409


def test_submit_case_id_stage_mismatch_returns_400(client, mint_token):
    token = mint_token("stu_11")
    # case_id 2 is the seeded assessment-stage case, not learning's.
    resp = client.post(
        "/submit",
        json={
            "token": token, "case_id": 2, "stage": "learning",
            "text": "x", "time_spent_seconds": 1,
        },
    )
    assert resp.status_code == 400


def test_submit_time_spent_out_of_range_returns_400(client, mint_token):
    token = mint_token("stu_12")
    case_id = _case_id(client, token)
    for bad_value in (-1, 7201):
        resp = client.post(
            "/submit",
            json={
                "token": token, "case_id": case_id, "stage": "learning",
                "text": "x", "time_spent_seconds": bad_value,
            },
        )
        assert resp.status_code == 400, f"expected 400 for time_spent_seconds={bad_value}"


def test_reset_clears_progress_and_submissions(client, mint_token):
    token = mint_token("stu_13")
    case_id = _case_id(client, token)
    client.post(
        "/submit",
        json={"token": token, "case_id": case_id, "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )
    assert client.get(f"/case?token={token}").json()["stage"] == "assessment"

    resp = client.post("/reset", json={"token": token})
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}

    # Back to a fresh learning-stage case, as if this student had never
    # submitted anything.
    data = client.get(f"/case?token={token}").json()
    assert data["stage"] == "learning"
    assert data["position"] == 1


def test_reset_is_blocked_during_test_stage(client, mint_token):
    """The actual cheating vector issue #27 exists to close: without this
    guard, a student partway through the graded exam who doesn't like how
    it's going could reset and retry with a case sequence they now
    remember. Drive stu_14 all the way to the test stage, then confirm
    reset is refused and progress is left completely untouched."""
    token = mint_token("stu_14")
    for stage in ("learning", "assessment"):
        case_id = _case_id(client, token)
        body = {"token": token, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = "3"
            body["modifier_s"] = False
        client.post("/submit", json=body)

    assert client.get(f"/case?token={token}").json()["stage"] == "test"

    resp = client.post("/reset", json={"token": token})
    assert resp.status_code == 403

    # Still exactly where it was -- the blocked attempt didn't partially
    # apply (e.g. clearing submissions but not progress, or vice versa).
    data = client.get(f"/case?token={token}").json()
    assert data["stage"] == "test"
    assert data["position"] == 1


def test_reset_still_allowed_after_completion(client, mint_token):
    """The one existing, legitimate use of reset (per main.py's own
    docstring: "once a student reaches 'complete', this is the way back
    to a fresh learning-stage case") must keep working -- the test-stage
    guard should be specific to "test", not accidentally block
    "complete" too."""
    token = mint_token("stu_15")
    for stage in ("learning", "assessment", "test"):
        case_id = _case_id(client, token)
        body = {"token": token, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = "3"
            body["modifier_s"] = False
        client.post("/submit", json=body)

    assert client.get(f"/case?token={token}").json() == {"complete": True}

    resp = client.post("/reset", json={"token": token})
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}
    assert client.get(f"/case?token={token}").json()["stage"] == "learning"


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
