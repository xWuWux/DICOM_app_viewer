"""scripts/dicom-patient-consistency.py on SYNTHETIC studies only."""

import importlib.util
from pathlib import Path

import pydicom
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

pydicom.config.settings.writing_validation_mode = pydicom.config.WARN
SCRIPT = Path(__file__).resolve().parent.parent / "dicom-patient-consistency.py"
spec = importlib.util.spec_from_file_location("consistency", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def make(path: Path, patient: str, study: str, series: str, sop: str, number: int):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    meta.MediaStorageSOPInstanceUID = sop
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = patient
    ds.PatientID = patient
    ds.StudyInstanceUID = study
    ds.SeriesInstanceUID = series
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = sop
    ds.InstanceNumber = number
    ds.Rows = ds.Columns = 2
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = b"\x01\x00" * 4
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path)


def build(tmp: Path, odd_numbers=(3, 4), clash=False):
    for n in range(1, 7):
        make(tmp / "d" / f"{n}.dcm", "Pat_A", ".1.2", ".1.2.9", f".1.2.9.{n}", n)
    for n in odd_numbers:
        make(tmp / "d" / f"{n}.dcm", "Pat_B", ".1.2", ".1.2.9", f".1.2.9.{n}", n)  # same slice, wrong patient label
    if clash:
        make(tmp / "d" / "dup.dcm", "Pat_B", ".1.2", ".1.2.9", ".1.2.9.1", 1)  # duplicates a majority SOP UID
    # a clean second study
    for n in range(1, 4):
        make(tmp / "e" / f"{n}.dcm", "Pat_C", ".3.4", ".3.4.9", f".3.4.9.{n}", n)
    return tmp / "d", tmp / "e"


def test_consistent_data_passes(tmp_path, capsys):
    make(tmp_path / "ok" / "1.dcm", "Pat_A", ".1", ".1.1", ".1.1.1", 1)
    assert mod.main([str(tmp_path / "ok")]) == 0
    assert "OK" in capsys.readouterr().out


def test_mixed_study_is_reported_and_fails_without_fix(tmp_path, capsys):
    d, _ = build(tmp_path)
    assert mod.main([str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert "images to correct: 2" in out.out and "Pat_" not in out.out + out.err


def test_fix_writes_corrected_copies_only_and_never_touches_originals(tmp_path, capsys):
    d, _ = build(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.dcm")}
    fixed = tmp_path / "fixed"
    assert mod.main([str(tmp_path), "--fix-out", str(fixed)]) == 0
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.dcm") if "fixed" not in p.parts} == {p: b for p, b in before.items()}
    corrected = sorted(fixed.rglob("*.dcm"))
    assert len(corrected) == 2
    for p in corrected:
        ds = pydicom.dcmread(p)
        assert (str(ds.PatientID), str(ds.PatientName)) == ("Pat_A", "Pat_A")
        assert ds.PixelData == b"\x01\x00" * 4  # pixels preserved
    skip = (fixed / "skip.txt").read_text().split()
    assert sorted(skip) == ["d/3.dcm", "d/4.dcm"]
    assert "Pat_" not in capsys.readouterr().out


def test_colliding_odd_images_are_refused(tmp_path):
    build(tmp_path, clash=True)
    assert mod.main([str(tmp_path), "--fix-out", str(tmp_path / "fixed")]) == 2
    assert not (tmp_path / "fixed").exists()


def test_a_tie_between_identities_is_refused(tmp_path):
    for n in (1, 2):
        make(tmp_path / "t" / f"a{n}.dcm", "Pat_A", ".9", ".9.1", f".9.1.{n}", n)
    for n in (3, 4):
        make(tmp_path / "t" / f"b{n}.dcm", "Pat_B", ".9", ".9.1", f".9.1.{n}", n)
    assert mod.main([str(tmp_path / "t"), "--fix-out", str(tmp_path / "fx")]) == 2


@pytest.mark.parametrize("arg", ["/nonexistent-dir-xyz"])
def test_missing_directory(arg):
    assert mod.main([arg]) == 2


def test_images_without_a_study_uid_are_refused_never_one_pseudo_study(tmp_path, capsys):
    # issue #160 review N2: empty StudyInstanceUIDs used to collapse into one
    # bucket, so the majority repair would relabel images from DIFFERENT
    # studies as if they were one study's odd images.
    make(tmp_path / "x" / "1.dcm", "Pat_A", "", ".7.1", ".7.1.1", 1)
    make(tmp_path / "y" / "1.dcm", "Pat_B", "", ".8.1", ".8.1.1", 1)
    rc = mod.main([str(tmp_path), "--fix-out", str(tmp_path / "fx")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no StudyInstanceUID" in captured.err
    assert "Pat_" not in captured.out + captured.err
    assert not (tmp_path / "fx").exists()


def test_empty_study_uid_also_refused_in_report_mode(tmp_path, capsys):
    make(tmp_path / "x" / "1.dcm", "Pat_A", "", ".7.1", ".7.1.1", 1)
    assert mod.main([str(tmp_path)]) == 2
    assert "no StudyInstanceUID" in capsys.readouterr().err
