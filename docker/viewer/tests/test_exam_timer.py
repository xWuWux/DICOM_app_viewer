"""Exam-panel timer (API 1.3.0): DOM-level tests, both pages.

Same pattern as test_stage_indicator.py: the API is mocked through Playwright routes, so
only a served viewer at --base-url is needed. Pins the contract: the timer appears on the
test stage only, directly under the stage bar, shows the SERVER's elapsed seconds
(time on the case + time in the whole test), ticks forward locally, renders hostile or
malformed values as 00:00:00 instead of markup, and disappears once the form is replaced.
"""

import json

import pytest

PAGES = ["watermark.html", "grading-panel.html"]
TOKEN = "t" * 43
OPTIONS = [["0", "Kategoria 0"], ["2", "Kategoria 2"], ["4A", "Kategoria 4A"]]


def _case(stage, timer=None):
    case = {
        "complete": False,
        "case_id": 1,
        "stage": stage,
        "position": 1,
        "total_in_stage": 2,
        "title": "Przypadek testowy",
        "orthanc_study_uid": "1.2.3",
    }
    if stage != "learning":
        case["category_options"] = OPTIONS
    if timer is not None:
        case["timer"] = timer
    return json.dumps(case)


def _route_case(page, body):
    page.route("**/api/case", lambda r: r.fulfill(status=200, content_type="application/json", body=body))


def _open(page, base_url, path):
    page.goto(f"{base_url}/{path}?student_id=T&session_id=1#token={TOKEN}")
    page.wait_for_selector("#grading-panel button", timeout=15000)


@pytest.mark.parametrize("path", PAGES)
def test_test_stage_shows_both_timers_under_the_stage_bar(page, base_url, path):
    _route_case(page, _case("test", {"case_elapsed_seconds": 125, "exam_elapsed_seconds": 3725}))
    page.clock.install()  # freeze ticking: the first render must show exactly the server values
    _open(page, base_url, path)
    assert page.inner_text("#timer-case") == "00:02:05"
    assert page.inner_text("#timer-exam") == "01:02:05"
    # directly below the navigation stage bar
    assert page.evaluate(
        "() => document.querySelector('#grading-panel .stage-bar').nextElementSibling.className"
    ) == "exam-timer"


@pytest.mark.parametrize("path", PAGES)
def test_timer_ticks_forward_from_the_server_values(page, base_url, path):
    _route_case(page, _case("test", {"case_elapsed_seconds": 59, "exam_elapsed_seconds": 3599}))
    page.clock.install()
    _open(page, base_url, path)
    page.clock.run_for(3000)
    assert page.inner_text("#timer-case") == "00:01:02"
    assert page.inner_text("#timer-exam") == "01:00:02"


@pytest.mark.parametrize("stage", ["learning", "assessment"])
@pytest.mark.parametrize("path", PAGES)
def test_no_timer_before_the_test_stage(page, base_url, path, stage):
    _route_case(page, _case(stage))
    _open(page, base_url, path)
    assert page.locator("#grading-panel .exam-timer").count() == 0


@pytest.mark.parametrize("path", PAGES)
def test_hostile_or_malformed_values_render_as_zero_not_markup(page, base_url, path):
    _route_case(page, _case("test", {"case_elapsed_seconds": "<img src=x onerror=alert(1)>", "exam_elapsed_seconds": -5}))
    page.clock.install()
    _open(page, base_url, path)
    assert page.inner_text("#timer-case") == "00:00:00"
    assert page.inner_text("#timer-exam") == "00:00:00"
    assert page.locator("#grading-panel .exam-timer img").count() == 0


@pytest.mark.parametrize("path", PAGES)
def test_timer_disappears_after_the_answer_is_submitted(page, base_url, path):
    _route_case(page, _case("test", {"case_elapsed_seconds": 10, "exam_elapsed_seconds": 10}))
    page.route("**/api/submit", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
    page.clock.install()
    _open(page, base_url, path)
    page.select_option("#category-input", "2")
    page.click('input[name="modifier-s"][value="no"]')
    page.click("#submit-btn")
    page.wait_for_selector("#next-btn", timeout=15000)
    assert page.locator("#grading-panel .exam-timer").count() == 0
    page.clock.run_for(2000)  # the interval stops itself; nothing re-appears
    assert page.locator("#grading-panel .exam-timer").count() == 0
