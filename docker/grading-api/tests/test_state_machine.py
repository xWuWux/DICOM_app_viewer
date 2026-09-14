"""
Unit tests for grading-api's 3-stage Lung-RADS state machine (issue #8).

These exist to protect the pedagogically/security-critical invariants this
project keeps stating in prose (README.md, CLAUDE.md, main.py's own
docstring) but had never had automated, isolated coverage for:
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


def _case_id(client, student_id):
    return client.get(f"/case?student_id={student_id}").json()["case_id"]


def test_fresh_student_starts_at_learning_stage(client):
    resp = client.get("/case?student_id=stu_1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["complete"] is False
    assert data["stage"] == "learning"
    assert data["position"] == 1
    assert data["total_in_stage"] == 1
    # Learning stage has no structured category picker -- category_options
    # is only added for assessment/test (see main.py's get_case()).
    assert "category_options" not in data


def test_case_response_never_leaks_ground_truth_or_reference_report(client):
    """The single most important invariant this whole feature exists to
    enforce, checked across all three stages generically (not just
    "learning doesn't leak") so a future field added to the /case response
    can't quietly reintroduce this."""
    student = "stu_leak_check"
    seen_stages = []
    while True:
        data = client.get(f"/case?student_id={student}").json()
        if data.get("complete"):
            break
        seen_stages.append(data["stage"])
        assert "ground_truth_category" not in data
        assert "ground_truth_modifier_s" not in data
        assert "reference_report" not in data

        body = {
            "student_id": student,
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


def test_learning_submit_reveals_reference_report_only(client):
    case_id = _case_id(client, "stu_2")
    resp = client.post(
        "/submit",
        json={
            "student_id": "stu_2",
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


def test_learning_submit_advances_to_assessment_stage(client):
    case_id = _case_id(client, "stu_3")
    client.post(
        "/submit",
        json={
            "student_id": "stu_3",
            "case_id": case_id,
            "stage": "learning",
            "text": "x",
            "time_spent_seconds": 1,
        },
    )
    data = client.get("/case?student_id=stu_3").json()
    assert data["stage"] == "assessment"
    assert data["position"] == 1
    assert data["category_options"]  # dropdown options present now


def test_assessment_submit_correct_category_reveals_ground_truth(client):
    # Drive stu_4 through learning first (mechanics already covered above;
    # here we're only checking assessment's own reveal behavior).
    student = "stu_4"
    case_id = _case_id(client, student)
    client.post(
        "/submit",
        json={"student_id": student, "case_id": case_id, "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )
    case_id = _case_id(client, student)
    resp = client.post(
        "/submit",
        json={
            "student_id": student, "case_id": case_id, "stage": "assessment",
            "category": "3", "modifier_s": False, "time_spent_seconds": 10,
        },
    )
    data = resp.json()
    assert data["correct"] is True
    assert data["ground_truth_category"] == "3"
    assert data["ground_truth_modifier_s"] is False


def test_assessment_submit_incorrect_category_still_reveals_ground_truth(client):
    """Assessment always reveals ground truth, correct or not -- only the
    test stage withholds it entirely. Getting this backwards (e.g. hiding
    ground truth on a wrong answer) would break the stage's whole
    cross-check purpose."""
    student = "stu_5"
    case_id = _case_id(client, student)
    client.post(
        "/submit",
        json={"student_id": student, "case_id": case_id, "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )
    case_id = _case_id(client, student)
    resp = client.post(
        "/submit",
        json={
            "student_id": student, "case_id": case_id, "stage": "assessment",
            "category": "1", "modifier_s": False, "time_spent_seconds": 10,
        },
    )
    data = resp.json()
    assert data["correct"] is False
    assert data["ground_truth_category"] == "3"


def test_test_stage_submit_reveals_nothing(client):
    """The other core invariant: no correct/incorrect feedback, no ground
    truth, nothing -- an empty object, not just a missing key or two."""
    student = "stu_6"
    for stage in ("learning", "assessment"):
        case_id = _case_id(client, student)
        body = {"student_id": student, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = "3"
            body["modifier_s"] = False
        client.post("/submit", json=body)

    case_id = _case_id(client, student)
    resp = client.post(
        "/submit",
        json={
            "student_id": student, "case_id": case_id, "stage": "test",
            "category": "4A", "modifier_s": True, "time_spent_seconds": 10,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {}


def test_completing_all_stages_marks_complete(client):
    student = "stu_7"
    for stage in ("learning", "assessment", "test"):
        case_id = _case_id(client, student)
        body = {"student_id": student, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = "3"
            body["modifier_s"] = False
        client.post("/submit", json=body)

    data = client.get(f"/case?student_id={student}").json()
    assert data == {"complete": True}


def test_results_before_completion(client):
    resp = client.get("/results?student_id=stu_8")
    assert resp.json() == {"complete": False}


def test_results_after_completion_reports_accuracy(client):
    student = "stu_9"
    for stage, category in (("learning", None), ("assessment", "3"), ("test", "4A")):
        case_id = _case_id(client, student)
        body = {"student_id": student, "case_id": case_id, "stage": stage,
                 "time_spent_seconds": 1}
        if stage == "learning":
            body["text"] = "x"
        else:
            body["category"] = category  # both submitted correctly here
            body["modifier_s"] = stage == "test"
        client.post("/submit", json=body)

    data = client.get(f"/results?student_id={student}").json()
    assert data["complete"] is True
    assert data["test_total"] == 1
    assert data["test_correct"] == 1
    assert data["accuracy"] == 1.0
    assert data["breakdown"] == [
        {"ground_truth": "4A", "submitted": "4A", "correct": True}
    ]


def test_submit_stage_mismatch_returns_409(client):
    student = "stu_10"
    case_id = _case_id(client, student)  # student is actually at "learning"
    resp = client.post(
        "/submit",
        json={
            "student_id": student, "case_id": case_id, "stage": "assessment",
            "category": "3", "modifier_s": False, "time_spent_seconds": 1,
        },
    )
    assert resp.status_code == 409


def test_submit_case_id_stage_mismatch_returns_400(client):
    student = "stu_11"
    # case_id 2 is the seeded assessment-stage case, not learning's.
    resp = client.post(
        "/submit",
        json={
            "student_id": student, "case_id": 2, "stage": "learning",
            "text": "x", "time_spent_seconds": 1,
        },
    )
    assert resp.status_code == 400


def test_submit_time_spent_out_of_range_returns_400(client):
    student = "stu_12"
    case_id = _case_id(client, student)
    for bad_value in (-1, 7201):
        resp = client.post(
            "/submit",
            json={
                "student_id": student, "case_id": case_id, "stage": "learning",
                "text": "x", "time_spent_seconds": bad_value,
            },
        )
        assert resp.status_code == 400, f"expected 400 for time_spent_seconds={bad_value}"


def test_reset_clears_progress_and_submissions(client):
    student = "stu_13"
    case_id = _case_id(client, student)
    client.post(
        "/submit",
        json={"student_id": student, "case_id": case_id, "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )
    assert client.get(f"/case?student_id={student}").json()["stage"] == "assessment"

    resp = client.post("/reset", json={"student_id": student})
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}

    # Back to a fresh learning-stage case, as if this student had never
    # submitted anything.
    data = client.get(f"/case?student_id={student}").json()
    assert data["stage"] == "learning"
    assert data["position"] == 1


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_students_are_isolated_from_each_other(client):
    """Progressing one student must never affect another's state --
    the state machine is keyed entirely on student_id."""
    case_id = _case_id(client, "stu_a")
    client.post(
        "/submit",
        json={"student_id": "stu_a", "case_id": case_id, "stage": "learning",
              "text": "x", "time_spent_seconds": 1},
    )

    stu_a_data = client.get("/case?student_id=stu_a").json()
    stu_b_data = client.get("/case?student_id=stu_b").json()

    assert stu_a_data["stage"] == "assessment"
    assert stu_b_data["stage"] == "learning"  # untouched by stu_a's submission
