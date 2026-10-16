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
    exp, no_uid = mod.fixture_expectations([str(FIXTURE_CT)])
    assert no_uid == []
    assert len(exp) == 1
    entry = next(iter(exp.values()))
    assert entry["files"] == ["CT_small.dcm"]
    assert entry["instances"] == 1 and entry["series"] == 1


def test_check_fixtures_matched_ok_missing_names_the_file(capsys):
    exp, _ = mod.fixture_expectations([str(FIXTURE_CT)])
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


# ---- the #160 shared-tags bug: real response shapes, keyword-free contract --
# Orthanc's /studies/{id}/shared-tags is keyed by HEX tags ("0020,000d");
# reading it by keyword returned "" for every study, so cases mode matched 0
# against any real Orthanc since it shipped. These mocks use the VERIFIED
# 26.9.1 shapes (spot-checked against a throwaway instance, issue #238 A
# review) so they cannot drift back into the fantasy shape that hid the bug.

REAL_UID = "1.2.840.113619.2.55.3.1234"  # plausible CT_small-shape public uid


def _fake_orthanc(monkeypatch, records=1):
    calls = []

    def fake_get_json(base, auth, path, timeout):
        calls.append(path)
        if path == "/studies":
            return [f"s{i}" for i in range(records)]
        if path.startswith("/studies/") and path.endswith("/series"):
            return [{"ID": "se1", "Instances": ["in1", "in2"], "Status": "Complete"}]
        if path.startswith("/studies/"):
            return {"ID": path.split("/")[2], "MainDicomTags": {"StudyInstanceUID": REAL_UID}}
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(mod, "_get_json", fake_get_json)
    return calls


def test_fetchers_use_maindicomtags_not_shared_tags(monkeypatch):
    calls = _fake_orthanc(monkeypatch)
    assert mod.fetch_study_uids("b", "u", "p") == [REAL_UID]
    assert mod.fetch_study_inventory("b", "u", "p") == {REAL_UID: {"series": 1, "instances": 2, "records": 1}}
    # the regression contract itself: no request may EVER touch shared-tags
    assert not any("shared-tags" in c for c in calls)


def test_shared_tags_hex_shape_is_untouched(monkeypatch):
    # even if an Orthanc returned keyword-keyed shared-tags (the old mock
    # fantasy), the code must not depend on it -- proved by _get_json above
    # never requesting the endpoint; here we additionally verify a detail
    # response WITHOUT MainDicomTags degrades to "" rather than KeyError.
    monkeypatch.setattr(mod, "_get_json", lambda b, a, p, t: {"ID": "s0"})
    assert mod.fetch_study_uids("b", "u", "p") == [""]


def test_uid_only_fixture_names_the_real_cause(tmp_path, capsys):
    import pydicom

    ds = pydicom.dcmread(str(FIXTURE_CT))
    del ds.StudyInstanceUID
    broken = tmp_path / "no-uid.dcm"
    ds.save_as(str(broken))
    assert mod.main(["--fixtures", str(broken)]) == 2
    # credentials ARE required before the network attempt; supply one so the
    # failure we assert on is the StudyInstanceUID message, not the auth one
    import os as _os

    _os.environ.setdefault("ORTHANC_PASSWORD", "sentinel-not-used")
    capsys.readouterr()
    assert mod.main(["--fixtures", str(broken)]) == 2
    assert "no StudyInstanceUID" in capsys.readouterr().err


def test_unreadable_fixture_names_files_not_orthanc(tmp_path, capsys):
    garbage = tmp_path / "garbage.dcm"
    garbage.write_bytes(b"not dicom at all")
    import os as _os

    _os.environ.setdefault("ORTHANC_PASSWORD", "sentinel-not-used")
    assert mod.main(["--fixtures", str(garbage)]) == 2
    err = capsys.readouterr().err
    assert "garbage.dcm" in err and "Orthanc" not in err
