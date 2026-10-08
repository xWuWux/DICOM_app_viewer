"""Cases from a local file (GRADING_CASES_FILE): validation, seeding, and the
guarantee that a bad file never leaks its content into an error message.

Synthetic data only: the real cases (answer key, reports, study UIDs) live in a
git-ignored local file and must never appear in this repository.
"""

import json

import pytest

from app import db as db_module
from app.cases_file import CasesFileError, load_cases

STAGES = ["learning", "assessment", "test"]
CATS = {"0", "1", "2", "3", "4A", "4B", "4X"}
SENTINEL_REPORT = "SENTINEL-REPORT-TEXT-MUST-NOT-LEAK"
SENTINEL_UID = ".987654.321098"


def _case(stage, idx, **over):
    base = {
        "stage": stage,
        "order_index": idx,
        "orthanc_study_uid": SENTINEL_UID,
        "title": f"Przypadek {idx + 1}",
        "ground_truth_category": "2",
        "ground_truth_modifier_s": 0,
        "reference_report": SENTINEL_REPORT,
    }
    base.update(over)
    return base


def _valid():
    return [_case("learning", 0), _case("learning", 1), _case("assessment", 0), _case("test", 0)]


def _write(tmp_path, data):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_valid_file_loads_in_seed_tuple_order(tmp_path):
    rows = load_cases(_write(tmp_path, _valid()), STAGES, CATS)
    assert len(rows) == 4
    stage, idx, uid, title, cat, mod, report = rows[0]
    assert (stage, idx, uid, cat, mod, report) == ("learning", 0, SENTINEL_UID, "2", 0, SENTINEL_REPORT)


def test_a_leading_dot_uid_is_accepted(tmp_path):
    # the received studies have UIDs with a stripped root, e.g. ".123456.654321"
    load_cases(_write(tmp_path, _valid()), STAGES, CATS)


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (lambda d: d.__setitem__(0, _case("nope", 0)), "stage is not one of"),
        (lambda d: d.__setitem__(0, _case("learning", -1)), "order_index must be a non-negative integer"),
        (lambda d: d.__setitem__(0, _case("learning", 0, orthanc_study_uid="1.2.x")), "not a DICOM UID"),
        (lambda d: d.__setitem__(0, _case("learning", 0, ground_truth_category="9")), "not a known Lung-RADS"),
        (lambda d: d.__setitem__(0, _case("learning", 0, ground_truth_modifier_s=2)), "must be 0 or 1"),
        (
            lambda d: d.__setitem__(0, _case("learning", 0, reference_report="  ")),
            "reference_report must be a non-empty",
        ),
        (lambda d: d.append(_case("learning", 0)), "duplicate"),
        (lambda d: d.__setitem__(1, _case("learning", 3)), "without gaps"),
        (lambda d: d[0].update(surprise=1), "unknown field"),
        (lambda d: d[0].pop("title"), "missing field"),
        (lambda d: d.__setitem__(0, "text"), "must be an object"),
    ],
)
def test_invalid_entries_are_rejected_without_echoing_values(tmp_path, mutate, expect):
    data = _valid()
    mutate(data)
    with pytest.raises(CasesFileError) as exc:
        load_cases(_write(tmp_path, data), STAGES, CATS)
    msg = str(exc.value)
    assert expect in msg
    assert SENTINEL_REPORT not in msg and SENTINEL_UID not in msg


def test_a_stage_without_cases_is_rejected(tmp_path):
    data = [c for c in _valid() if c["stage"] != "test"]
    with pytest.raises(CasesFileError, match="stage test has no case"):
        load_cases(_write(tmp_path, data), STAGES, CATS)


@pytest.mark.parametrize("content", ["", "not json", "{}", "[]"])
def test_unparseable_or_empty_files_are_rejected(tmp_path, content):
    p = tmp_path / "cases.json"
    p.write_text(content)
    with pytest.raises(CasesFileError):
        load_cases(str(p), STAGES, CATS)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(CasesFileError, match="does not exist"):
        load_cases(str(tmp_path / "nope.json"), STAGES, CATS)


def test_init_db_seeds_from_the_file_instead_of_the_placeholders(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("GRADING_CASES_FILE", _write(tmp_path, _valid()))
    db_module.init_db()
    conn = db_module.get_connection()
    rows = conn.execute(
        "SELECT stage, order_index, orthanc_study_uid FROM cases ORDER BY stage, order_index"
    ).fetchall()
    conn.close()
    assert [(r[0], r[1]) for r in rows] == [("assessment", 0), ("learning", 0), ("learning", 1), ("test", 0)]
    assert all(r[2] == SENTINEL_UID for r in rows)
    assert not any("PLACEHOLDER" in str(r) for r in rows)


def test_invalid_file_stops_startup_with_a_value_free_message(tmp_path, monkeypatch):
    data = _valid()
    data[0]["ground_truth_category"] = "9"
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("GRADING_CASES_FILE", _write(tmp_path, data))
    with pytest.raises(SystemExit) as exc:
        db_module.init_db()
    assert "GRADING_CASES_FILE" in str(exc.value)
    assert SENTINEL_REPORT not in str(exc.value) and SENTINEL_UID not in str(exc.value)


def test_a_second_start_does_not_reseed_and_warns(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("GRADING_CASES_FILE", _write(tmp_path, _valid()))
    db_module.init_db()
    with caplog.at_level(logging.WARNING):
        db_module.init_db()
    conn = db_module.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 4
    conn.close()
    assert any(r.getMessage() == "cases_file_ignored" for r in caplog.records)


def test_without_the_variable_the_placeholders_are_used(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.delenv("GRADING_CASES_FILE", raising=False)
    db_module.init_db()
    conn = db_module.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM cases WHERE reference_report LIKE 'PLACEHOLDER%'").fetchone()[0] == 3
    conn.close()
