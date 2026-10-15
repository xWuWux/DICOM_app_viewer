"""scripts/verify-cases-in-orthanc.py (issue #160 review S1) -- no live Orthanc:
fetch_study_uids is monkeypatched, the cases file is SYNTHETIC. The property
under test is the output contract: counts and stage/order codes only, never a
UID/title/report value from the (answer-key-bearing) cases file.
"""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "verify-cases-in-orthanc.py"
spec = importlib.util.spec_from_file_location("verify_cases", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SENTINEL_UID = ".987654.321098"  # leading-dot shape of the real exports (#162)
SENTINEL_MISSING = ".555000.555"


def _cases(*uids):
    return [
        {"stage": f"stage{i}", "order_index": i, "orthanc_study_uid": uid}
        for i, uid in enumerate(uids)
    ]


def _write(tmp_path, cases):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    return p


def test_all_present_exits_0_and_prints_counts_only(tmp_path, monkeypatch, capsys):
    f = _write(tmp_path, _cases(SENTINEL_UID))
    monkeypatch.setenv("ORTHANC_PASSWORD", "test-only")
    monkeypatch.setattr(mod, "fetch_study_uids", lambda *a, **k: [SENTINEL_UID])
    assert mod.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "cases: 1" in out and "matched: 1" in out
    assert SENTINEL_UID not in out


def test_missing_case_reports_stage_order_and_exits_1(tmp_path, monkeypatch, capsys):
    f = _write(tmp_path, _cases(SENTINEL_UID, SENTINEL_MISSING))
    monkeypatch.setenv("ORTHANC_PASSWORD", "test-only")
    monkeypatch.setattr(mod, "fetch_study_uids", lambda *a, **k: [SENTINEL_UID, SENTINEL_UID])
    assert mod.main([str(f)]) == 1
    out = capsys.readouterr().out
    assert "stage=stage1 order_index=1" in out
    assert SENTINEL_MISSING not in out


def test_duplicate_orthanc_records_warn_the_split_signature(tmp_path, monkeypatch, capsys):
    # one StudyInstanceUID served by two Orthanc study records = issue #162 split
    f = _write(tmp_path, _cases(SENTINEL_UID))
    monkeypatch.setenv("ORTHANC_PASSWORD", "test-only")
    monkeypatch.setattr(mod, "fetch_study_uids", lambda *a, **k: [SENTINEL_UID, SENTINEL_UID])
    assert mod.main([str(f)]) == 0
    assert "duplicate study record(s)" in capsys.readouterr().out


def test_connection_failure_exits_2_one_line_no_traceback(tmp_path, monkeypatch, capsys):
    f = _write(tmp_path, _cases(SENTINEL_UID))
    monkeypatch.setenv("ORTHANC_PASSWORD", "test-only")

    def boom(*a, **k):
        raise OSError("internal-host.example:443 refused")

    monkeypatch.setattr(mod, "fetch_study_uids", boom)
    assert mod.main([str(f)]) == 2
    err = capsys.readouterr().err
    assert "cannot read study list" in err
    assert "Traceback" not in err and "internal-host" not in err


def test_unusable_cases_file_exits_2_without_printing_its_content(tmp_path, monkeypatch, capsys):
    p = tmp_path / "cases.json"
    p.write_text("[" + SENTINEL_UID, encoding="utf-8")  # truncated JSON
    monkeypatch.setenv("ORTHANC_PASSWORD", "test-only")
    assert mod.main([str(p)]) == 2
    err = capsys.readouterr().err
    assert "build-cases-file.py format" in err
    assert SENTINEL_UID not in err

# ---- --fixtures mode (issue #238 point A): the inverse question ------------
# cases mode: "is every graded answer present". fixture mode: "is every file
# a throwaway stack UPLOADED actually served back, grouping intact". Same
# output policy tested here: counts + committed file names, never a UID.

FIXTURE_CT = Path(__file__).resolve().parents[2] / "sample-data" / "CT_small.dcm"


def _inv(uid, series=1, instances=1, records=1):
    return {uid: {"series": series, "instances": instances, "records": records}}


def test_fixture_expectations_over_a_committed_fixture():
    exp = mod.fixture_expectations([str(FIXTURE_CT)])
    assert len(exp) == 1
    entry = next(iter(exp.values()))
    assert entry["files"] == ["CT_small.dcm"]
    assert entry["instances"] == 1 and entry["series"] == 1


def test_check_fixtures_matched_ok_missing_names_the_file(capsys):
    exp = mod.fixture_expectations([str(FIXTURE_CT)])
    uid = next(iter(exp))
    assert mod.check_fixtures(exp, _inv(uid)) == 0
    assert mod.check_fixtures(exp, {}) == 1
    out = capsys.readouterr().out
    assert "CT_small.dcm" in out
    assert uid not in out  # the no-UID policy holds for shared code paths too


def test_check_fixtures_split_study_is_never_merged_away(capsys):
    # 1+0 records sum to a count that "matches" -- a #162 split must fail on
    # the record count alone, which is why fetch_study_inventory keeps them.
    exp = {"u": {"files": ["a.dcm"], "instances": 1, "series": 1}}
    assert mod.check_fixtures(exp, _inv("u", records=2)) == 1
    assert "duplicate" in capsys.readouterr().out


def test_check_fixtures_grouping_mismatch(capsys):
    exp = {"u": {"files": ["a.dcm"], "instances": 2, "series": 1}}
    assert mod.check_fixtures(exp, _inv("u", instances=1)) == 1
    assert "grouping mismatch" in capsys.readouterr().out


def test_main_requires_exactly_one_mode(capsys):
    assert mod.main([]) == 2
    assert mod.main(["cases.json", "--fixtures", "x.dcm"]) == 2
