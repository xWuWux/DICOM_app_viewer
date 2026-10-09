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
