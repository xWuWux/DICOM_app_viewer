"""The telemetry contract is a contract, not prose (issue #113).

docs/observability.md is what an operator writes an alert against. A doc that
drifts from the code is worse than no doc: the alert keeps firing on a name
the service stopped emitting, and nothing fails loudly. So these tests parse
the doc and compare it with the single source of truth in
app/telemetry.py -- rename a signal or a field on one side and CI says so.

Deliberately strict both ways: a signal in the code but not the doc is an
undocumented signal (nobody can be expected to alert on it), and a signal in
the doc but not the code is a lie.
"""

import re
from pathlib import Path

import pytest

from app import telemetry

DOC = Path(__file__).resolve().parents[3] / "docs" / "observability.md"
APP_DIR = Path(__file__).resolve().parents[1] / "app"

SIGNAL_ROW = re.compile(
    r"^\|\s*`(?P<name>grading_[a-z0-9_]+)`\s*\|(?P<type>[^|]*)\|(?P<unit>[^|]*)\|(?P<labels>[^|]*)\|"
)
EVENT_ROW = re.compile(r"^\|\s*`(?P<name>[a-z][a-z0-9_]*)`\s*\|")
BACKTICKED = re.compile(r"`([^`]+)`")


def _section(heading: str) -> str:
    """Text between `## <heading>` and the next `## ` heading."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(f"## {heading}")
    rest = text[start + len(heading) + 3 :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _documented_signals() -> dict:
    signals = {}
    for line in _section("Signals (metrics)").splitlines():
        match = SIGNAL_ROW.match(line)
        if not match:
            continue
        labels_cell = match.group("labels")
        labels = tuple(BACKTICKED.findall(labels_cell))
        signals[match.group("name")] = {
            "type": match.group("type").strip(),
            "unit": match.group("unit").strip(),
            "labels": labels,
        }
    return signals


def test_the_doc_exists_and_has_the_signal_table():
    assert DOC.is_file(), DOC
    assert _documented_signals(), "the Signals table parsed to nothing -- heading or layout changed"


def test_every_code_signal_is_documented():
    documented = _documented_signals()
    missing = sorted(set(telemetry.SIGNAL_SPEC) - set(documented))
    assert not missing, f"signals emitted by the service but absent from docs/observability.md: {missing}"


def test_every_documented_signal_exists_in_code():
    documented = _documented_signals()
    stale = sorted(set(documented) - set(telemetry.SIGNAL_SPEC))
    assert not stale, f"docs/observability.md documents signals the service does not emit: {stale}"


@pytest.mark.parametrize("name", sorted(telemetry.SIGNAL_SPEC))
def test_documented_type_unit_and_labels_match_the_code(name):
    spec = telemetry.SIGNAL_SPEC[name]
    documented = _documented_signals()[name]
    assert documented["type"] == spec["type"], f"{name}: type drifted"
    assert documented["unit"] == spec["unit"], f"{name}: unit drifted"
    assert documented["labels"] == spec["labels"], f"{name}: labels drifted"


def test_every_documented_event_is_actually_emitted_somewhere():
    """Catches a renamed event: the doc promises a `message` value, the code
    must contain that literal. Cheap grep, but it is the difference between a
    contract and a wish."""
    sources = "\n".join(path.read_text(encoding="utf-8") for path in APP_DIR.glob("*.py"))
    rows = _section("Events (log lines)").splitlines()
    documented = [EVENT_ROW.match(line).group("name") for line in rows if EVENT_ROW.match(line)]
    assert documented, "the Events table parsed to nothing"
    absent = sorted(name for name in documented if f'"{name}"' not in sources)
    assert not absent, f"events documented but never emitted: {absent}"


def test_the_forbidden_fields_section_names_the_real_risks():
    """Non-vacuity for the forbidden list: the three leaks this project has
    actually had to close (#62 DB paths in errors, #93 answers/tokens in 422
    logs, #119 client-supplied request ids) must be named as forbidden."""
    text = DOC.read_text(encoding="utf-8").lower()
    for needle in ("session token", "submitted_text", "x-request-id", "exception"):
        assert needle in text, needle


def test_the_doc_states_the_counter_reset_limitation():
    """The single most misread property of an in-process counter. If this
    paragraph disappears, someone will write an alert on an absolute value."""
    assert "counters reset on process restart" in DOC.read_text(encoding="utf-8").lower()
