"""Study picker (API 1.4.0): DOM-level tests, both pages, API mocked through Playwright routes.

Pins: the picker appears on browsable stages only, under the stage bar; it lists the studies in
the order grading-api gives, marks the current case, preselects the viewed one; choosing a study
POSTs /api/view with that case id and shows the "preview" note; titles render as text; the test
stage and a single-study list show no picker; the form keeps answering the current case.
"""

import json

import pytest

PAGES = ["watermark.html", "grading-panel.html"]
TOKEN = "t" * 43
OPTIONS = [["0", "Kategoria 0"], ["2", "Kategoria 2"], ["4A", "Kategoria 4A"]]


def _case(stage, browsable):
    case = {
        "complete": False,
        "case_id": 12,
        "stage": stage,
        "position": 2,
        "total_in_stage": 2,
        "title": "Przypadek bieżący",
        "orthanc_study_uid": "1.2.3",
        "browsable": browsable,
    }
    if stage != "learning":
        case["category_options"] = OPTIONS
    return json.dumps(case)


ITEMS = [
    {"case_id": 11, "stage": "learning", "position": 1, "title": "Pierwszy", "current": False, "viewing": False},
    {"case_id": 12, "stage": "learning", "position": 2, "title": "Przypadek bieżący", "current": True, "viewing": True},
]


def _route(page, stage="learning", browsable=True, items=ITEMS, view_calls=None):
    page.route("**/api/case", lambda r: r.fulfill(status=200, content_type="application/json", body=_case(stage, browsable)))
    page.route(
        "**/api/studies",
        lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"browsable": bool(items), "items": items})
        ),
    )

    def view(route):
        body = json.loads(route.request.post_data)
        if view_calls is not None:
            view_calls.append(body)
        current = body["case_id"] == 12
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"viewing_case_id": body["case_id"], "current": current}),
        )

    page.route("**/api/view", view)


def _open(page, base_url, path):
    page.goto(f"{base_url}/{path}?student_id=T&session_id=1#token={TOKEN}")
    page.wait_for_selector("#grading-panel button", timeout=15000)


@pytest.mark.parametrize("path", PAGES)
def test_picker_lists_studies_under_the_stage_bar(page, base_url, path):
    _route(page)
    _open(page, base_url, path)
    page.wait_for_selector("#study-select", timeout=15000)
    labels = page.locator("#study-select option").all_inner_texts()
    assert labels == ["Nauka 1: Pierwszy", "Nauka 2: Przypadek bieżący (bieżący)"]
    assert page.input_value("#study-select") == "12"
    assert page.evaluate(
        "() => document.querySelector('#grading-panel .stage-bar').nextElementSibling.id"
    ) == "study-picker"


@pytest.mark.parametrize("path", PAGES)
def test_choosing_a_study_posts_the_view_pointer_and_keeps_the_form(page, base_url, path):
    calls = []
    _route(page, view_calls=calls)
    _open(page, base_url, path)
    page.wait_for_selector("#study-select", timeout=15000)
    page.select_option("#study-select", "11")
    page.wait_for_function("() => document.getElementById('study-note').textContent.length > 0", timeout=15000)
    assert calls == [{"token": TOKEN, "case_id": 11}]
    assert "podgląd" in page.inner_text("#study-note")
    assert page.locator("#text-input").count() == 1  # still answering the current (learning) case
    page.select_option("#study-select", "12")  # back to the current case clears the note
    page.wait_for_function("() => document.getElementById('study-note').textContent === ''", timeout=15000)


@pytest.mark.parametrize("path", PAGES)
def test_no_picker_on_the_test_stage(page, base_url, path):
    _route(page, stage="test", browsable=False, items=[])
    _open(page, base_url, path)
    page.wait_for_timeout(300)
    assert page.locator("#study-picker, #study-select").count() == 0


@pytest.mark.parametrize("path", PAGES)
def test_no_picker_when_there_is_only_one_study(page, base_url, path):
    _route(page, items=ITEMS[1:])
    _open(page, base_url, path)
    page.wait_for_timeout(300)
    assert page.locator("#study-select").count() == 0


@pytest.mark.parametrize("path", PAGES)
def test_titles_render_as_text_not_markup(page, base_url, path):
    hostile = [dict(ITEMS[0], title="<img src=x onerror=alert(1)>"), ITEMS[1]]
    _route(page, items=hostile)
    _open(page, base_url, path)
    page.wait_for_selector("#study-select", timeout=15000)
    assert page.locator("#study-picker img").count() == 0
    assert "<img src=x" in page.locator("#study-select option").first.inner_text()
