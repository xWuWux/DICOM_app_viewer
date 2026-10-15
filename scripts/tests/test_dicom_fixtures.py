"""Integrity of the COMMITTED DICOM fixtures (issue #238 point A).

The unit suites elsewhere guard the CHECKERS; this file guards the DATA that
every later stage takes for granted. For every .dcm committed under sample-data/
(owner decision 2026-10-15: fixtures are the committed set ONLY, fetched public
samples never enter CI) it pins, in the order the pipeline consumes them:

  1. parseability + the identity/grouping UIDs the loaders and the grading-api
     cases file require (the study-UID rule is imported live from
     docker/grading-api/app/cases_file.py, not restated, so a rule change there
     cannot silently outdate this file),
  2. self-consistent pixel data (geometry, transfer syntax, buffer length),
  3. the pipeline's own guards over the whole committed set: the
     patient-consistency majority rule runs clean (zero offenders, zero
     refusals, no mixed studies),
  4. negative controls: mutated copies of the fixtures must be CAUGHT by the
     very guards above -- proof the guards are not vacuous.

Orthanc-side coverage (studies actually SERVED with matching grouping) lives in
`scripts/verify-cases-in-orthanc.py --fixtures`, run by scripts/smoke-test.sh --
it needs the live stack and has no business in this offline file.
"""

import importlib.util
import math
import os
from pathlib import Path

import pydicom
import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "sample-data"


def _load(name: str, rel: str):
    """Import a hyphenated-path module without sys.path games (the pattern the
    provision/create_session tests already use)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_consistency = _load("dicom_patient_consistency", "scripts/dicom-patient-consistency.py")
_cases_file = _load("grading_cases_file", "docker/grading-api/app/cases_file.py")
_weasis_uri = _load("weasis_case_uri", "docker/kasm-workspace-weasis/weasis_case_uri.py")


def _committed_fixtures():
    # git ls-files, not os.walk: locally the directory also holds fetched
    # public samples (git-ignored, huge, network-originated) which are NOT
    # this gate's data and are not loaded by any throwaway stack.
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "sample-data"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [ROOT / line for line in out if line.lower().endswith(".dcm")]


FIXTURES = _committed_fixtures()


def test_committed_fixture_set_is_what_the_docs_name():
    """A silent rename/removal of a fixture would empty every other gate here
    AND the smoke job's loader -- fail loudly instead of vacuously green."""
    names = {p.name for p in FIXTURES}
    assert {"CT_small.dcm", "MR_small.dcm"} <= names, names


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_identity_and_grouping_uids_are_pipeline_valid(fixture):
    ds = pydicom.dcmread(fixture, stop_before_pixels=True)
    study_uid = str(ds.get("StudyInstanceUID", "")).strip()
    series_uid = str(ds.get("SeriesInstanceUID", "")).strip()
    sop_uid = str(ds.get("SOPInstanceUID", "")).strip()
    assert study_uid and series_uid and sop_uid, fixture.name
    # the rule grading-api applies to the cases file (STUDY_UID_RE: digits and
    # dots, must END in a digit -- the #160 N3 rule against the "." and
    # trailing-dot quirks of the received exports, issue #162)
    assert _cases_file.STUDY_UID_RE.fullmatch(study_uid), (fixture.name, study_uid)
    # and the Weasis URI builder accepts it too (its own _UID_RE + length cap)
    assert _weasis_uri._UID_RE.fullmatch(study_uid), (fixture.name, study_uid)
    assert str(ds.get("PatientID", "")).strip() or str(ds.get("PatientName", "")).strip()


# Uncompressed transfer syntaxes whose pixel buffer length is exactly
# frames * rows * cols * bytes-per-sample; the committed set uses these, and a
# future fixture in an encoded syntax must arrive with its own checker, not a
# relaxed version of this one.
_UNCOMPRESSED = {
    "1.2.840.10008.1.2.1",  # explicit VR little endian
    "1.2.840.10008.1.2.1.99",  # deflated explicit VR little endian
}


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_pixel_data_matches_its_own_geometry(fixture):
    ds = pydicom.dcmread(fixture)
    ts = str(ds.file_meta.TransferSyntaxUID)
    assert ts in _UNCOMPRESSED, (fixture.name, ts)
    rows, cols = int(ds.Rows), int(ds.Columns)
    bits = int(ds.BitsAllocated)
    frames = int(ds.get("NumberOfFrames", 1))
    samples = int(ds.get("SamplesPerPixel", 1))
    assert rows > 0 and cols > 0 and bits in (1, 8, 16) and samples == 1
    assert str(ds.PhotometricInterpretation).startswith("MONOCHROME")
    bytes_per_unit = math.ceil(bits / 8)
    expected = rows * cols * frames * samples * bytes_per_unit
    assert len(ds.PixelData) == expected, (fixture.name, len(ds.PixelData), expected)


def test_whole_committed_set_passes_the_consistency_guards():
    studies, empty_uid = _consistency.scan(str(SAMPLES))
    offenders, refused, mixed = _consistency.plan(studies)
    assert empty_uid == [], "a committed fixture lost its StudyInstanceUID"
    assert (offenders, refused, mixed) == ([], 0, 0)
    assert len(studies) >= 2, "fixtures stopped describing distinct studies"
    # no SOP/instance collisions WITHIN a study (the grouping invariant the
    # loaders silently rely on)
    for uid, items in studies.items():
        sops = [i[3] for i in items]
        assert len(set(sops)) == len(sops), uid


# ---------- negative controls: the guards must bite --------------------------


def _mutate(tmp_path, fixture, name, tweak):
    ds = pydicom.dcmread(fixture)
    tweak(ds)
    out = tmp_path / name
    ds.save_as(str(out))
    return out


def test_missing_study_uid_is_refused_not_collapsed(tmp_path):
    # issue #160 review N2: images without a StudyInstanceUID must never
    # collapse into one pseudo-study whose majority would relabel them.
    a = _mutate(tmp_path, FIXTURES[0], "a.dcm", lambda ds: ds.__delitem__("StudyInstanceUID"))
    b = _mutate(tmp_path, FIXTURES[1], "b.dcm", lambda ds: ds.__delitem__("StudyInstanceUID"))
    studies, empty_uid = _consistency.scan(str(tmp_path))
    assert sorted(empty_uid) == sorted(os.path.relpath(p, tmp_path) for p in (a, b))
    assert studies == {}


def test_same_study_two_patients_is_caught_as_mixed(tmp_path):
    # deliberately tie the two identities (one image each): plan() must REFUSE,
    # not pick a "majority" out of a coin flip.
    twin = FIXTURES[0]
    _mutate(tmp_path, twin, "ct.dcm", lambda ds: None)
    _mutate(
        tmp_path,
        FIXTURES[1],
        "mr.dcm",
        lambda ds: setattr(ds, "StudyInstanceUID", str(pydicom.dcmread(twin).StudyInstanceUID)),
    )
    studies, _ = _consistency.scan(str(tmp_path))
    offenders, refused, mixed = _consistency.plan(studies)
    assert mixed == 1
    assert refused >= 1 or offenders == []  # tie -> refused; a winner -> offenders list


@pytest.mark.parametrize("tweak", [
    pytest.param(lambda ds: setattr(ds, "Rows", int(ds.Rows) + 2), id="geometry-lies"),
    # even truncation: DICOM value lengths are even, so dropping one byte is
    # silently re-padded on save and the buffer comes back whole.
    pytest.param(lambda ds: ds.__setattr__("PixelData", ds.PixelData[:-2]), id="pixel-truncated"),
])
def test_broken_geometry_is_caught(tmp_path, tweak):
    broken = _mutate(tmp_path, FIXTURES[0], "broken.dcm", tweak)
    ds = pydicom.dcmread(broken)
    with pytest.raises((AssertionError, ValueError)):
        # re-run THIS file's invariant over the mutated copy
        expected = (
            int(ds.Rows) * int(ds.Columns) * int(ds.get("NumberOfFrames", 1))
            * int(ds.get("SamplesPerPixel", 1)) * math.ceil(int(ds.BitsAllocated) / 8)
        )
        assert len(ds.PixelData) == expected
