#!/usr/bin/env python3
"""Load-time PHI assertion gate (issue #238 point B1).

ANONYMISATION IS UPSTREAM OF THIS PIPELINE (owner decision 2026-10-15): the
de-identification of received cohorts happens before ingestion, by the
sending institution / operator tooling. This script does NOT anonymise
anything and never will -- it ASSERTS the boundary held: files still carrying
direct patient identifiers are REFUSED at load time, before a single byte
reaches Orthanc, so a forgotten pre-step cannot silently put PHI into the
on-premise store.

Two tiers, calibrated against this repo's own committed fixtures and the
public samples (issue #238 B1 recon of 2026-10-15), mirroring DICOM PS3.15's
Basic Application Level Collection:

  REJECT (D/Z-type patient identity: remove-or-empty is the only profile
  option that keeps them harmless -- a non-placeholder value means
  de-identification did not run or did not finish):
    birth dates/times, other-patient-IDs (+ sequences), other names,
    address, telephone, mother's birth name, admission/visit identifiers,
    patient/study/visit free-text comments.
  WARN (institutional/device/staff metadata: replacement or retention are
  both legitimate profile choices, so presence is not a refusal -- but an
  operator loading a "de-identified" cohort should SEE that real-sounding
  institution/station/physician strings survived):
    referring physician, operators, reading/consulting physician,
    institution name/address/department, station, device serial, accession.

Placeholder rule: a value passes if empty/whitespace, only punctuation
(dashes/dots/underscores), all-zero digits, or a recognised placeholder word
(unknown/anonymous/anon*/masked/redacted/hidden/none/null/n-a/test/sample/
private/withheld, EN/PL forms). The placeholder set is deliberately small:
"JFK IMAGING CENTER" is NOT a placeholder -- which is exactly why institution
tags are WARN-tier and D-tier tags refuse.

Output policy: TAG NAMES + counts + one example file path per tag, NEVER a
value. Values are PHI by definition; echoing "PatientBirthDate=1955-03-05"
into a terminal or CI log would defeat the entire gate.

Exit codes: 0 clean (warnings allowed, printed), 1 findings in REJECT tier,
2 configuration/usage error (including --allow-tag without a reason -- the
operator must document why an identity tag is tolerated, e.g. a public
training corpus that genuinely carries one).

Usage:
  python3 scripts/phi-gate.py DIR [--exclude-file LIST] [--allow-tag TAG=REASON ...]
  # DIR relative paths in --exclude-file LIST skip the scan (they are the
  # files the caller will NOT upload, e.g. a consistency repair skip-list).
"""

import argparse
import os
import re
import sys

REJECT_TAGS = {
    "PatientBirthDate",
    "PatientBirthTime",
    "OtherPatientIDs",
    "OtherPatientIDsSequence",
    "OtherPatientNames",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "PatientMotherBirthName",
    "AdmissionID",
    "VisitComments",
    "PatientComments",
    "StudyComments",
}

WARN_TAGS = {
    "ReferringPhysicianName",
    "OperatorsName",
    "NameOfPhysiciansReadingStudy",
    "ConsultingPhysicianName",
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "StationName",
    "DeviceSerialNumber",
    "AccessionNumber",
}

_PLACEHOLDER_WORDS = {
    "unknown", "unknown.", "anonymous", "anon", "anonymized", "anonymised",
    "anonymizied",  # seen in the wild, keep tolerated
    "masked", "redacted", "hidden", "none", "null", "n/a", "na", "n.a.",
    "test", "sample", "private", "withheld", "undisclosed", "notavailable",
    "not available", "not given", "brak",  # PL: "none"
    "wykre",  # PL anonymiser stubs sometimes truncate "Wykreowano..."
}
_PUNCT_ONLY = re.compile(r"^[ .:_\-/]+$")
_PL_PREFIX = re.compile(r"^anoni[a-z]*$")  # anon, anoni, anonim, anonym...


def is_placeholder(value):
    """True if a tag value is a de-identification placeholder, False for real
    data. Names-only rule on purpose: nothing here parses dates or regexes
    patient-name shapes -- if a value is not punctuation/zero/placeholder
    word, it counts as real and the D-tier refuses it."""
    s = str(value).strip()
    if not s:
        return True
    low = s.lower()
    if low in _PLACEHOLDER_WORDS or _PL_PREFIX.match(low):
        return True
    if _PUNCT_ONLY.match(s):
        return True
    if re.fullmatch(r"0+", s):
        return True
    return False


def _scan_dataset(ds, rel, findings, depth=0):
    """One file's tags -> findings dict {tag: {"count": n, "sample": rel}}."""
    for tag in REJECT_TAGS | WARN_TAGS:
        if tag not in ds:
            continue
        value = ds[tag].value if hasattr(ds[tag], "value") else None
        # sequences: the presence question is whether ANY child carries a
        # real identifier value; recurse one level (VR SQ nests datasets)
        vr = getattr(ds[tag], "VR", "")
        if vr == "SQ" and depth < 2:
            clean = True
            try:
                for item in value or []:
                    for elem in item:
                        if elem.VR == "SQ":
                            continue
                        if not is_placeholder(elem.value):
                            clean = False
                            break
                    if not clean:
                        break
            except TypeError:
                clean = not is_placeholder(value)
            if not clean:
                findings.setdefault(tag, {"count": 0, "sample": rel})
                findings[tag]["count"] += 1
            continue
        if not is_placeholder(value):
            findings.setdefault(tag, {"count": 0, "sample": rel})
            findings[tag]["count"] += 1


def scan_dir(root, exclude=()):
    """-> findings dict {tag: {"count", "sample"}} over *.dcm under root."""
    import pydicom

    excluded = set(os.path.normpath(e) for e in exclude)
    findings = {}
    scanned = 0
    for dirpath, _, files in os.walk(root):
        for f in sorted(files):
            if not f.lower().endswith(".dcm"):
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            if os.path.normpath(rel) in excluded:
                continue
            try:
                ds = pydicom.dcmread(full, stop_before_pixels=True)
            except Exception:
                continue  # unreadable files are the consistency scanner's
                          # problem; this gate only judges what it can read
            scanned += 1
            _scan_dataset(ds, rel, findings)
    return findings, scanned


def report(findings, scanned, out=None, allow=None):
    """Print counts per tag (NEVER values). -> (reject_count, warn_count)
    after removing allowed tags."""
    out = out or sys.stdout
    allow = allow or {}
    reject = {t: v for t, v in findings.items() if t in REJECT_TAGS and t not in allow}
    warned = {t: v for t, v in findings.items() if t in WARN_TAGS}
    for tag, reason in sorted(allow.items()):
        if tag in findings:
            print(f"allowed (operator attests): {tag} x{findings[tag]['count']} -- {reason}", file=out)
    for tag in sorted(reject):
        info = reject[tag]
        print(f"PHI REFUSED: {tag} x{info['count']} (e.g. {info['sample']})", file=out)
    for tag in sorted(warned):
        info = warned[tag]
        print(f"note (replacement-tolerant tier): {tag} x{info['count']} (e.g. {info['sample']})", file=out)
    print(f"phi-gate: scanned {scanned} file(s); refused tags: {len(reject)}; noted tags: {len(warned)}", file=out)
    return len(reject), len(warned)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir")
    ap.add_argument("--exclude-file", help="file of DIR-relative paths to skip (the caller's upload skip-list)")
    ap.add_argument("--allow-tag", action="append", default=[],
                    help="TAG=REASON: tolerate a REJECT-tier tag, reason REQUIRED and echoed (auditable)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.dir):
        print("ERROR: directory does not exist", file=sys.stderr)
        return 2
    allow = {}
    for spec in a.allow_tag:
        tag, sep, reason = spec.partition("=")
        if not sep or not reason.strip():
            print(f"ERROR: --allow-tag needs TAG=REASON (reason required), got: {tag}", file=sys.stderr)
            return 2
        if tag not in REJECT_TAGS:
            print(f"ERROR: --allow-tag only applies to reject-tier tags; {tag} is "
                  + ("warn-tier (presence alone never refuses)" if tag in WARN_TAGS else "not tracked at all"),
                  file=sys.stderr)
            return 2
        allow[tag] = reason.strip()
    exclude = ()
    if a.exclude_file:
        if not os.path.isfile(a.exclude_file):
            print("ERROR: --exclude-file does not exist", file=sys.stderr)
            return 2
        with open(a.exclude_file, encoding="utf-8") as fh:
            exclude = [line.strip() for line in fh if line.strip()]
    findings, scanned = scan_dir(a.dir, exclude)
    if scanned == 0:
        print("ERROR: no readable .dcm files under DIR", file=sys.stderr)
        return 2
    reject, _warned = report(findings, scanned, allow=allow)
    if reject:
        print("phi-gate: REFUSED. Anonymisation runs upstream of ingestion; fix the source "
              "or --allow-tag TAG=REASON for genuinely public data.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
