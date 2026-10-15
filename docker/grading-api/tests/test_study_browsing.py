"""Study browsing (owner request 2026-10-14): in the learning and assessment stages the doctor may
open (view only) the study of any case of the stages reached so far; answering stays in order on
the current case; the test stage stays locked.

Pinned here:
  - GET /studies lists exactly the browsable cases, in course order, and leaks no ground truth,
    reference report or study UID;
  - POST /view moves the pointer; /case then carries view_study_uid for Weasis while the form
    still answers the current case;
  - opening the current case clears the pointer; a submit clears it too;
  - outside the browsable set (a later stage, the test stage) /view is refused with 403.
"""

H = "X-Grading-Token"


def _case(client, token):
    return client.get("/case", headers={H: token}).json()


def _submit_current(client, token):
    case = _case(client, token)
    body = {"token": token, "case_id": case["case_id"], "stage": case["stage"]}
    if case["stage"] == "learning":
        body["text"] = "x"
    else:
        body["category"] = "3"
        body["modifier_s"] = False
    assert client.post("/submit", json=body).status_code == 200
    return case


def test_learning_lists_only_learning_studies_without_secrets(client, mint_token):
    token = mint_token("stu_browse_list")
    body = client.get("/studies", headers={H: token}).json()
    assert body["browsable"] is True
    assert [i["stage"] for i in body["items"]] == ["learning"]
    item = body["items"][0]
    assert item["current"] is True and item["viewing"] is True
    assert set(item) == {"case_id", "stage", "position", "title", "current", "viewing"}


def test_assessment_lists_learning_and_assessment_in_course_order(client, mint_token):
    token = mint_token("stu_browse_order")
    _submit_current(client, token)
    stages = [i["stage"] for i in client.get("/studies", headers={H: token}).json()["items"]]
    assert stages == ["learning", "assessment"]


def test_view_points_weasis_at_another_study_while_the_form_keeps_the_current_case(client, mint_token):
    token = mint_token("stu_browse_view")
    learning = _submit_current(client, token)
    assessment = _case(client, token)

    resp = client.post("/view", json={"token": token, "case_id": learning["case_id"]})
    assert resp.status_code == 200 and resp.json() == {"viewing_case_id": learning["case_id"], "current": False}

    case = _case(client, token)
    assert case["case_id"] == assessment["case_id"]  # still answering the current case
    assert case["view_study_uid"] == learning["orthanc_study_uid"]
    items = {i["case_id"]: i for i in client.get("/studies", headers={H: token}).json()["items"]}
    assert items[learning["case_id"]]["viewing"] and not items[assessment["case_id"]]["viewing"]


def test_viewing_the_current_case_clears_the_pointer(client, mint_token):
    token = mint_token("stu_browse_back")
    learning = _submit_current(client, token)
    assessment = _case(client, token)
    client.post("/view", json={"token": token, "case_id": learning["case_id"]})
    resp = client.post("/view", json={"token": token, "case_id": assessment["case_id"]})
    assert resp.json()["current"] is True
    assert "view_study_uid" not in _case(client, token)


def test_submitting_clears_the_pointer(client, mint_token):
    token = mint_token("stu_browse_submit")
    learning = _submit_current(client, token)
    client.post("/view", json={"token": token, "case_id": learning["case_id"]})
    _submit_current(client, token)  # answers the assessment case (the current one)
    case = _case(client, token)
    assert case["stage"] == "test"
    assert "view_study_uid" not in case


def test_the_test_stage_is_locked(client, mint_token):
    token = mint_token("stu_browse_test")
    learning = _submit_current(client, token)
    _submit_current(client, token)
    case = _case(client, token)
    assert case["stage"] == "test" and case["browsable"] is False
    assert client.get("/studies", headers={H: token}).json() == {"browsable": False, "items": []}
    for case_id in (learning["case_id"], case["case_id"]):
        resp = client.post("/view", json={"token": token, "case_id": case_id})
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "VIEW_NOT_ALLOWED"


def test_a_later_stage_cannot_be_opened_early(client, mint_token):
    token = mint_token("stu_browse_ahead")
    from app import db as db_module

    conn = db_module.get_connection()
    try:
        test_id = conn.execute("SELECT id FROM cases WHERE stage = 'test' LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    resp = client.post("/view", json={"token": token, "case_id": test_id})
    assert resp.status_code == 403


def test_studies_and_view_require_a_token(client):
    assert client.get("/studies").status_code == 401
    assert client.post("/view", json={"token": "never-minted", "case_id": 1}).status_code == 401


def test_migration_adds_the_view_pointer_after_a_backup(tmp_path, monkeypatch):
    import glob
    import sqlite3

    from app import db as db_module

    path = str(tmp_path / "grading-pre-browse.db")
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO progress (student_id, stage, case_order_index, case_assigned_at) "
        "VALUES ('stu_old', 'learning', 0, 1.0)"
    )
    conn.execute("ALTER TABLE progress DROP COLUMN view_case_id")  # the database as it was before
    conn.commit()
    conn.close()

    db_module.init_db()

    assert len(glob.glob(f"{path}.pre-migration-*.sqlite.bak")) == 1
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT stage, view_case_id FROM progress WHERE student_id = 'stu_old'").fetchone()
    finally:
        conn.close()
    assert row == ("learning", None)
