"""scripts/phi-gate.py (issue #238 point B1): the assertion-only boundary.

The gate judges TAGS, so the honest negative controls here are committed
public fixtures mutated in tmp_path (a real-valued PatientBirthDate planted
on CT_small). Three properties are pinned: (1) classification -- placeholder
vs real value, reject tier vs warn tier; (2) fail-closed behaviour, reasons
required for every tolerance; (3) the output policy -- tag names, counts and
paths yes, VALUES never: a printed identifier defeats the gate it came from.
"""

import importlib.util
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
    findings, scanned = mod.scan_dir(str(tmp_path))
    assert scanned == 1 and "PatientBirthDate" in findings
    reject, _warn = mod.report(findings, scanned)
    out = capsys.readouterr().out
    assert reject >= 1
    assert "PatientBirthDate" in out
    assert "1955" not in out  # THE output policy: never a value


def test_placeholder_birthdate_passes(tmp_path):
    _copy_with(tmp_path, CT, "ct.dcm", PatientBirthDate="00000000")
    findings, _ = mod.scan_dir(str(tmp_path))
    assert "PatientBirthDate" not in findings


def test_alternate_id_sequence_is_refused(tmp_path):
    # CT_small carries OtherPatientIDsSequence with fake-but-real-shaped IDs;
    # a de-identified cohort must not -- refuse, this is what the gate is for.
    (tmp_path / "ct.dcm").write_bytes(CT.read_bytes())
    findings, _ = mod.scan_dir(str(tmp_path))
    assert "OtherPatientIDsSequence" in findings


def test_warn_tier_never_refuses(tmp_path):
    # MR_small survives with warn-tier tags only (institution, station,
    # device serial) -- presence alone must not block a legitimate load.
    (tmp_path / "mr.dcm").write_bytes(MR.read_bytes())
    findings, _ = mod.scan_dir(str(tmp_path))
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
    findings, scanned = mod.scan_dir(str(tmp_path), exclude=["bad.dcm"])
    assert scanned == 1 and "PatientBirthDate" not in findings
    # then through main() the way load-local-studies.sh calls it
    assert mod.main([str(tmp_path), "--exclude-file", str(skip)]) == 0


def test_no_dcm_is_config_error(tmp_path, capsys):
    assert mod.main([str(tmp_path)]) == 2


def test_missing_dir_is_config_error(tmp_path):
    assert mod.main([str(tmp_path / "nowhere")]) == 2
