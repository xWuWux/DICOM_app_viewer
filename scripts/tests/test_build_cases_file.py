"""scripts/build-cases-file.py with SYNTHETIC inputs only (the real spreadsheet,
answer key and study UIDs live in git-ignored local files, never in this repo)."""

import importlib.util
import json
import os
import stat
from pathlib import Path

import openpyxl
import pydicom
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

pydicom.config.settings.writing_validation_mode = pydicom.config.WARN
SCRIPT = Path(__file__).resolve().parent.parent / "build-cases-file.py"
spec = importlib.util.spec_from_file_location("build_cases_file", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SENTINEL_REPORT = "SENTINEL-REPORT-9f3a"
SENTINEL_CODE = "SENTINELCODE_7"


def make_dcm(path: Path, name: str, study_uid: str):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    meta.MediaStorageSOPInstanceUID = "1.2.3.4"
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = name
    ds.PatientID = name
    ds.StudyInstanceUID = study_uid
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = "1.2.3.4"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path)


def make_xlsx(path: Path, rows, header=("Anonim", "Opis", "Lung-Rads")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)


@pytest.fixture()
def env(tmp_path):
    codes = [f"Pat_{i}" for i in range(1, 6)]
    uids = [f".11111{i}.22222{i}" for i in range(1, 6)]  # leading-dot UIDs like the received data
    for c, u in zip(codes, uids):
        make_dcm(tmp_path / "dcm" / c / "1.dcm", c, u)
        make_dcm(tmp_path / "dcm" / c / "2.dcm", c, u)  # several images per study
    make_xlsx(tmp_path / "x.xlsx", [(c, f"{SENTINEL_REPORT} {c}", cat) for c, cat in zip(codes, [2, 3, "4a", "4B S", 1.0])])
    return tmp_path, codes, uids


def run(tmp, *extra):
    return mod.main(["--xlsx", str(tmp / "x.xlsx"), "--dicom-dir", str(tmp / "dcm"), "--out", str(tmp / "out" / "cases.json"), *extra])


def test_default_mapping_builds_a_valid_file(env, capsys):
    tmp, codes, uids = env
    assert run(tmp) == 0
    cases = json.loads((tmp / "out" / "cases.json").read_text())
    assert [(c["stage"], c["order_index"]) for c in cases] == [("learning", i) for i in range(5)] + [("assessment", 0), ("test", 0)]
    assert [c["orthanc_study_uid"] for c in cases[:5]] == uids  # read from the DICOM headers, dots preserved
    assert [c["ground_truth_category"] for c in cases[:5]] == ["2", "3", "4A", "4B", "1"]
    assert [c["ground_truth_modifier_s"] for c in cases[:5]] == [0, 0, 0, 1, 0]
    assert all(c["title"].startswith("Przypadek") and "Pat_" not in c["title"] for c in cases)
    # the file must load with the service's own validator
    import sys

    sys.path.insert(0, str(SCRIPT.parent.parent / "docker" / "grading-api"))
    from app.cases_file import load_cases

    load_cases(str(tmp / "out" / "cases.json"), ["learning", "assessment", "test"], {"0", "1", "2", "3", "4A", "4B", "4X"})


def test_output_is_private_and_stdout_has_no_values(env, capsys):
    tmp, codes, uids = env
    run(tmp)
    mode = stat.S_IMODE(os.stat(tmp / "out" / "cases.json").st_mode)
    assert mode == 0o600
    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert SENTINEL_REPORT not in text and not any(u in text for u in uids) and not any(c in text for c in codes)


def test_custom_stage_mapping(env):
    tmp, *_ = env
    assert run(tmp, "--stages", "learning=1,2", "assessment=3", "test=4,5") == 0
    cases = json.loads((tmp / "out" / "cases.json").read_text())
    assert [c["stage"] for c in cases] == ["learning", "learning", "assessment", "test", "test"]


@pytest.mark.parametrize(
    "stages",
    [["learning=all", "assessment=1"], ["learning=0", "assessment=1", "test=1"], ["learning=1,1", "assessment=1", "test=1"], ["learning=9", "assessment=1", "test=1"], ["nope=1", "assessment=1", "test=1"]],
)
def test_bad_stage_mapping_is_rejected(env, stages):
    tmp, *_ = env
    with pytest.raises(SystemExit):
        run(tmp, "--stages", *stages)


def test_unknown_code_is_rejected_without_naming_it(env, capsys):
    tmp, *_ = env
    make_xlsx(tmp / "x.xlsx", [(SENTINEL_CODE, "report", 2)])
    with pytest.raises(SystemExit):
        run(tmp)
    err = capsys.readouterr().err
    assert "row 2" in err and SENTINEL_CODE not in err


def test_bad_category_is_rejected(env, capsys):
    tmp, codes, _ = env
    make_xlsx(tmp / "x.xlsx", [(codes[0], "r", "9")])
    with pytest.raises(SystemExit):
        run(tmp)
    assert "Lung-Rads value is not a known category" in capsys.readouterr().err


def test_missing_column_is_rejected(env):
    tmp, codes, _ = env
    make_xlsx(tmp / "x.xlsx", [(codes[0], "r", 2)], header=("Anonim", "Opis", "Cat"))
    with pytest.raises(SystemExit):
        run(tmp)


def test_existing_output_is_not_overwritten_without_force(env):
    tmp, *_ = env
    assert run(tmp) == 0
    with pytest.raises(SystemExit):
        run(tmp)
    assert run(tmp, "--force") == 0


def test_a_few_mislabelled_images_do_not_break_the_match(env):
    tmp, codes, uids = env
    # 2 extra images of study 1 carry study 2's code, like the real data's labelling slips
    for k in (1, 2):
        make_dcm(tmp / "dcm" / "slip" / f"{k}.dcm", codes[1], uids[0])
    # the majority (2 images of the study's own code, plus the 2 slips) must still point at study 1 for code 1
    make_dcm(tmp / "dcm" / "extra" / "x.dcm", codes[0], uids[0])
    make_dcm(tmp / "dcm" / "extra" / "y.dcm", codes[0], uids[0])
    assert run(tmp) == 0
    cases = json.loads((tmp / "out" / "cases.json").read_text())
    assert [c["orthanc_study_uid"] for c in cases[:5]] == uids
