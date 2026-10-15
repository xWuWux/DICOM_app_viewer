"""scripts/phi-gate.py (issue #238 point B1): the assertion-only boundary.

The gate judges TAGS, so the honest negative controls here are committed
public fixtures mutated in tmp_path (a real-valued PatientBirthDate planted
on CT_small). Three properties are pinned: (1) classification -- placeholder
vs real value, reject tier vs warn tier; (2) fail-closed behaviour, reasons
required for every tolerance; (3) the output policy -- tag names, counts and
paths yes, VALUES never: a printed identifier defeats the gate it came from.
"""

import importlib.util
import os
from pathlib import Path

import pydicom
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("phi_gate", ROOT / "scripts" / "phi-gate.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CT = ROOT / "sample-data" / "CT_small.dcm"
MR = ROOT / "sample-data" / "MR_small.dcm"


def _copy_with(tmp_path, src, name, **setters):
    ds = pydicom.dcmread(src)
    for kw, val in setters.items():
        setattr(ds, kw, val)
    out = tmp_path / name
    ds.save_as(str(out))
    return out


# ---- classification ---------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "----", "--", "0000", "00000000", "unknown",
                                   "Anonymous", "anoni", "anonim", "brak", "n/a", "MASKED", "sample"])
def test_placeholders_pass(value):
    assert mod.is_placeholder(value)


@pytest.mark.parametrize("value", ["1955-03-05", "19550305", "Kowalska^Anna", "ABC123",
                                   "JFK IMAGING CENTER", "03-05-1955 ", "P-404"])
def test_real_values_are_not_placeholders(value):
    assert not mod.is_placeholder(value)


def test_real_planted_birthdate_is_refused_and_value_never_printed(tmp_path, capsys):
    _copy_with(tmp_path, CT, "ct.dcm", PatientBirthDate="19550305")
    findings, scanned, unreadable, nonstandard = mod.scan_dir(str(tmp_path))
    assert scanned == 1 and unreadable == [] and nonstandard == [] and "PatientBirthDate" in findings
    reject, _warn = mod.report(findings, scanned)
    out = capsys.readouterr().out
    assert reject >= 1
    assert "PatientBirthDate" in out
    assert "1955" not in out  # THE output policy: never a value


def test_placeholder_birthdate_passes(tmp_path):
    _copy_with(tmp_path, CT, "ct.dcm", PatientBirthDate="00000000")
    findings, _s, _u, _n = mod.scan_dir(str(tmp_path))
    assert "PatientBirthDate" not in findings


def test_alternate_id_sequence_is_refused(tmp_path):
    # CT_small carries OtherPatientIDsSequence with fake-but-real-shaped IDs;
    # a de-identified cohort must not -- refuse, this is what the gate is for.
    (tmp_path / "ct.dcm").write_bytes(CT.read_bytes())
    findings, _s, _u, _n = mod.scan_dir(str(tmp_path))
    assert "OtherPatientIDsSequence" in findings


def test_warn_tier_never_refuses(tmp_path):
    # MR_small survives with warn-tier tags only (institution, station,
    # device serial) -- presence alone must not block a legitimate load.
    (tmp_path / "mr.dcm").write_bytes(MR.read_bytes())
    findings, _s, _u, _n = mod.scan_dir(str(tmp_path))
    assert findings  # calibration guard: warn-tier tags ARE present
    assert not any(t in mod.REJECT_TAGS for t in findings)
    assert mod.main([str(tmp_path)]) == 0


# ---- fail-closed config ------------------------------------------------------


def test_main_refuses_real_birthdate(tmp_path, capsys):
    _copy_with(tmp_path, CT, "ct.dcm", PatientBirthDate="19550305")
    import os

    os.environ["HOME"] = str(tmp_path)  # phi-gate never reads env; belt for braces
    assert mod.main([str(tmp_path)]) == 1


def test_allow_tag_requires_reason(tmp_path, capsys):
    assert mod.main([str(tmp_path), "--allow-tag", "PatientBirthDate"]) == 2
    assert "TAG=REASON" in capsys.readouterr().err or "REASON" in capsys.readouterr().err


def test_allow_tag_only_for_reject_tier(tmp_path):
    assert mod.main([str(tmp_path), "--allow-tag", "InstitutionName=x"]) == 2
    assert mod.main([str(tmp_path), "--allow-tag", "NotATag=x"]) == 2


def test_allow_tag_with_reason_tolerates_and_echoes(tmp_path, capsys):
    _copy_with(tmp_path, CT, "ct.dcm", PatientBirthDate="19550305")
    # CT_small natively carries OtherPatientIDsSequence too -- tolerate both
    rc = mod.main([str(tmp_path),
                   "--allow-tag", "PatientBirthDate=public training corpus",
                   "--allow-tag", "OtherPatientIDsSequence=public training corpus"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "allowed (operator attests)" in out and "public training corpus" in out
    assert "1955" not in out


def test_exclude_file_skips_unuploaded_files(tmp_path, capsys):
    # good.dcm from MR_small: warn-tier only, no reject-tier tags at all
    _copy_with(tmp_path, MR, "good.dcm")
    _copy_with(tmp_path, CT, "bad.dcm", PatientBirthDate="19550305")
    skip = tmp_path / "skip.txt"
    skip.write_text("bad.dcm\n", encoding="utf-8")
    # scan-level first
    findings, scanned, unreadable, nonstandard = mod.scan_dir(str(tmp_path), exclude=["bad.dcm"])
    assert scanned == 1 and unreadable == [] and nonstandard == [] and "PatientBirthDate" not in findings
    # then through main() the way load-local-studies.sh calls it
    assert mod.main([str(tmp_path), "--exclude-file", str(skip)]) == 0


def test_no_dcm_is_config_error(tmp_path, capsys):
    assert mod.main([str(tmp_path)]) == 2


def test_missing_dir_is_config_error(tmp_path):
    assert mod.main([str(tmp_path / "nowhere")]) == 2


# ---- no fail-open: unjudgeable files are REFUSED, never skipped -------------
# Review proof: Orthanc stores files pydicom cannot parse (preamble-less
# DICOM with a planted birth date -> POST /instances HTTP 200). A scanner
# that "skips unreadables" would hand that file straight into the store.


def test_preamble_less_file_cannot_slip_through(tmp_path, capsys):
    # MR_small + planted birth date, saved, then the 128-byte preamble and
    # the DICM magic stripped: exactly the review repro.
    src = _copy_with(tmp_path, MR, "mr.dcm", PatientBirthDate="19550305")
    raw = src.read_bytes()
    stripped = raw[132:]
    src.write_bytes(stripped)
    assert mod.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "1955" not in out
    # the file needed force to read -> refused as malformed (or unparseable);
    # never as a judged-clean file:
    _f, scanned, unreadable, nonstandard = mod.scan_dir(str(tmp_path))
    assert scanned == 0 and (unreadable or nonstandard)


def test_truly_unparseable_file_is_refused_not_skipped(tmp_path, capsys):
    (tmp_path / "junk.dcm").write_bytes(os.urandom(4096))
    assert mod.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert ("unparseable" in out) or ("malformed" in out)
    _f, scanned, unreadable, nonstandard = mod.scan_dir(str(tmp_path))
    assert scanned == 0 and (unreadable + nonstandard) == ["junk.dcm"]


# ---- nested data: tracked tags count at ANY depth (decision (4)) ------------


def test_reject_tag_buried_two_levels_deep_is_caught(tmp_path, capsys):
    import pydicom as _pd

    inner = _pd.Dataset()
    inner.PatientBirthDate = "19550305"
    mid_sq = _pd.Dataset()
    mid_sq.PatientBleedingSequence if False else None  # (no such tag; use a real SQ keyword)
    mid = _pd.Dataset()
    mid.RequestAttributesSequence = [inner]      # level 2
    outer = _pd.Dataset()
    outer.RequestAttributesSequence = [mid]      # level 1, UNTRACKED parent
    base = _pd.dcmread(str(MR))
    base.RequestAttributesSequence = [outer]     # level 0
    bad = tmp_path / "buried.dcm"
    base.save_as(str(bad))
    findings, scanned, unreadable, nonstandard = mod.scan_dir(str(tmp_path))
    assert scanned == 1 and unreadable == [] and nonstandard == []
    assert "PatientBirthDate" in findings  # found through two untracked SQs
    assert mod.main([str(tmp_path)]) == 1
