"""The final screen's "Zakończ sesję" (doctors' request after the first Kasm run), both pages, API mocked.

The server revokes the doctor's token (POST /api/session/end, tested in grading-api); the panel asks once,
sends ONE request, and then shows "Sesja zakończona" and forgets the token. It exists only on the final screen."""

import json

import pytest
from test_review_flow import TOKEN, Api, _open

PAGES = ["watermark.html", "grading-panel.html"]


class EndApi(Api):
    def __init__(self, page, end_status=200, **kw):
        super().__init__(page, **kw)
        self.end_status = end_status
        page.route("**/api/session/end", self._end)

    def _end(self, route):
        self.calls.append("session/end")
        body = {"ended": True} if self.end_status == 200 else {"error_code": "X", "message": "boom"}
        route.fulfill(status=self.end_status, content_type="application/json", body=json.dumps(body))


def _final(page, base_url, path):
    page.goto(f"{base_url}/{path}?student_id=T&session_id=1#token={TOKEN}")
    page.wait_for_selector("#results-title", timeout=15000)


@pytest.mark.parametrize("path", PAGES)
def test_the_button_is_on_the_final_screen_under_the_score_and_asks_before_ending(page, base_url, path):
    api = EndApi(page, finalized=True, n=2)
    _final(page, base_url, path)
    btn = page.locator("#end-session-btn")
    assert btn.inner_text() == "Zakończ sesję" and btn.is_visible()
    # it sits above the case list, so it is reachable without scrolling a long exam
    box = page.evaluate("() => [document.querySelector('#end-session-box').getBoundingClientRect().top, document.querySelector('.answer-box').getBoundingClientRect().top]")
    assert box[0] < box[1]
    btn.click()
    assert "Czy na pewno zakończyć sesję?" in page.inner_text("#end-session-box")
    assert "session/end" not in api.calls  # nothing was sent yet
    page.click("#end-session-no")
    assert page.locator("#end-session-btn").is_visible()
    assert "session/end" not in api.calls


@pytest.mark.parametrize("path", PAGES)
def test_confirming_ends_the_session_once_and_forgets_the_token(page, base_url, path):
    api = EndApi(page, finalized=True)
    _final(page, base_url, path)
    page.click("#end-session-btn")
    page.dblclick("#end-session-yes")
    page.wait_for_selector("#session-ended")
    assert api.calls.count("session/end") == 1
    assert "Sesja zakończona" in page.inner_text("#grading-panel")
    assert page.evaluate("sessionStorage.length") == 0  # the token is gone from this tab
    page.reload()
    page.wait_for_selector("#session-expired", timeout=15000)  # a reload cannot bring the session back


@pytest.mark.parametrize("path", PAGES)
def test_a_failed_end_keeps_the_screen_and_can_be_retried(page, base_url, path):
    api = EndApi(page, end_status=500, finalized=True)
    _final(page, base_url, path)
    page.click("#end-session-btn")
    page.click("#end-session-yes")
    page.wait_for_function("document.querySelector('#end-session-error').textContent.length > 0")
    assert "Nie udało się zakończyć sesji" in page.inner_text("#end-session-error")
    assert page.locator("#end-session-yes").is_enabled()
    api.end_status = 200
    page.click("#end-session-yes")
    page.wait_for_selector("#session-ended")


@pytest.mark.parametrize("path", PAGES)
def test_a_401_means_the_session_is_already_over_and_counts_as_ended(page, base_url, path):
    EndApi(page, end_status=401, finalized=True)
    _final(page, base_url, path)
    page.click("#end-session-btn")
    page.click("#end-session-yes")
    page.wait_for_selector("#session-ended")


@pytest.mark.parametrize("path", PAGES)
def test_there_is_no_end_button_on_the_review_or_the_case_screens(page, base_url, path):
    EndApi(page, finalized=False)
    _open(page, base_url, path)  # the review screen (exam complete, not approved)
    assert page.locator("#end-session-btn").count() == 0
    assert "Zakończ sesję" not in page.inner_text("#grading-panel")
