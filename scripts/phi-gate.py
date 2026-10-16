#!/usr/bin/env python3
"""Load-time PHI assertion gate (issue #238 point B1).

ANONYMISATION IS UPSTREAM OF THIS PIPELINE (owner decision 2026-10-15): the
de-identification of received cohorts happens before ingestion, by the
sending institution / operator tooling. This script does NOT anonymise
anything and never will -- it ASSERTS the boundary held: files still carrying
direct patient identifiers, or that cannot be judged at all, are REFUSED at
load time, before a single byte reaches Orthanc, so a forgotten pre-step
cannot silently put PHI into the on-premise store.

Two tiers, calibrated against this repo's own committed fixtures and the
public samples (issue #238 B1 recon of 2026-10-15), mirroring DICOM PS3.15's
Basic Application Level Collection:

  REJECT (D/Z-type patient identity -- remove-or-empty is the only profile
  option that keeps them harmless; a non-placeholder value means
  de-identification did not run or did not finish):
    birth dates/times, other-patient-IDs (+ sequences), other names,
    address, telephone, mother's birth name, admission/visit identifiers,
    patient/study free-text comments.
  WARN (institutional/device/staff metadata -- replacement or retention are
  both legitimate profile choices, so presence is not a refusal, but an
    operator loading a "de-identified" cohort should SEE that real-sounding
    institution/station/physician strings survived):
    referring physician, operators, reading/physician/consulting names,
    institution name/address/department, station, device serial, accession.

What is deliberately NOT tracked (human decisions, 2026-10-15 -- do not
"fix" these later):
  * PatientName / PatientID: the upstream anonymiser REPLACES them with
    stable pseudonyms, and the whole pipeline (consistency majority, cases
    matching, grading keying) DEPENDS on those pseudonyms. Their mere
    presence is not PHI and no tool here checks them.
  * StudyID: learning series are keyed on it deliberately.
  * ContentDescription / ImageComments / series+study descriptions:
    teaching labels by intent; unchecked, not even warned.

Nested data (decision (4)): tracked tags count at ANY nesting depth, also
inside parent sequences that are themselves untracked. Inside a tracked
SEQUENCE the child check is keyword-agnostic: any real descendant value
refuses, whatever benign keyword it hides under.

Placeholder rule: a value passes if empty/whitespace, only punctuation
(dashes/dots/underscores), all-zero digits, or a recognised placeholder word
(unknown/anonymous/anon*/masked/redacted/hidden/none/null/n-a/test/sample/
private/withheld, EN/PL forms). The set is deliberately small: "JFK IMAGING
CENTER" is NOT a placeholder -- which is exactly why institution tags are
WARN-tier while the D-tier tags refuse.

Output policy: TAG NAMES + counts + one example file path per tag, NEVER a
value. Values are PHI by definition; echoing "PatientBirthDate=1955-03-05"
into a terminal or CI log would defeat the entire gate.

Exit codes: 0 clean (warnings allowed, printed), 1 findings in the REJECT
tier OR unparseable files (an unjudgeable file is never "clean": Orthanc
accepts files pydicom cannot parse -- issue #238 B1 review), 2 configuration
error (including --allow-tag without a reason: the operator must document
WHY an identity tag is tolerated, e.g. a public training corpus).

Usage:
  python3 scripts/phi-gate.py DIR [--exclude-file LIST] [--allow-tag TAG=REASON ...]
  # DIR relative paths in --exclude-file LIST skip the scan (the files the
  # caller will NOT upload, e.g. a consistency repair skip-list).
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
_PL_PREFIX = re.compile(r"^anoni[a-z]*$")
_ALL_ZERO = re.compile(r"^0+$")


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
    if _ALL_ZERO.match(s):
        return True
    return False


def _note(findings, tag, rel):
    findings.setdefault(tag, {"count": 0, "sample": rel})
    findings[tag]["count"] += 1


MAX_DEPTH = 10  # parsed DICOM nests shallowly; the cap is a cycle fuse


def _leaf_values(value, depth=0):
    """Every non-sequence descendant value inside a sequence value."""
    if depth > MAX_DEPTH:
        return
    items = value if isinstance(value, list) else [value]
    for item in items:
        if hasattr(item, "elements"):  # a pydicom Dataset
            yield from _dataset_leaf_values(item, depth + 1)
        else:
            yield item


def _dataset_leaf_values(ds, depth=0):
    for elem in ds:
        if elem.VR == "SQ":
            yield from _leaf_values(elem.value, depth)
        else:
            yield elem.value


def _scan_dataset(ds, rel, findings, depth=0):
    """Judge every tracked tag in this dataset, then recurse into every
    UNTRACKED sequence (decision (4): a tracked tag counts at ANY nesting
    depth, even inside parents that are themselves untracked).

    A tracked SEQUENCE refuses on any real descendant value, keyword-
    agnostic: OtherPatientIDsSequence hiding 'ABCD1234' under a benign
    keyword is still a real alternate ID. Tracked SCALARS follow the
    placeholder rule wherever they sit. Untracked scalars inside untracked
    sequences stay unchecked -- per the tier rulings, only TRACKED tags
    count when nested."""
    for tag in REJECT_TAGS | WARN_TAGS:
        if tag not in ds:
            continue
        elem = ds[tag]
        if elem.VR == "SQ":
            if any(not is_placeholder(v) for v in _leaf_values(elem.value)):
                _note(findings, tag, rel)
        elif not is_placeholder(elem.value):
            _note(findings, tag, rel)
    if depth >= MAX_DEPTH:
        return
    tracked = REJECT_TAGS | WARN_TAGS
    for elem in ds:
        keyword = getattr(elem.tag, "keyword", "") or ""
        if elem.VR == "SQ" and keyword not in tracked:
            for item in elem.value or []:
                if hasattr(item, "elements"):
                    _scan_dataset(item, rel, findings, depth + 1)


def scan_dir(root, exclude=()):
    """-> (findings, scanned, unreadable_paths) over every *.dcm under root.

    scanned + len(unreadable_paths) == the loader's own upload list (same
    *.dcm filter, same skip-list exclusion), so the counts close by
    construction and no file sits outside the judgement. A file pydicom
    refuses even with force=True lands in unreadable_paths: Orthanc ACCEPTS
    such files (a preamble-less DICOM with a planted birth date stored
    HTTP 200 in the review repro), so 'could not judge' must never mean
    'clean'."""
    import pydicom

    excluded = set(os.path.normpath(e) for e in exclude)
    findings = {}
    unreadable = []
    nonstandard = []
    scanned = 0
    for dirpath, _, files in os.walk(root):
        for f in sorted(files):
            if not f.lower().endswith(".dcm"):
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            if os.path.normpath(rel) in excluded:
                continue
            ds = None
            forced = False
            try:
                ds = pydicom.dcmread(full, stop_before_pixels=True)
            except Exception:
                try:
                    ds = pydicom.dcmread(full, stop_before_pixels=True, force=True)
                    forced = True
                except Exception:
                    unreadable.append(rel)
                    continue
            if forced:
                # refusing, not judging: force-parsing is pydicom guessing at
                # malformed input (VR UN blobs may hide nested datasets that
                # only Orthanc's parser will decode). Legitimate files never
                # need force, so this costs honest cohorts nothing.
                nonstandard.append(rel)
                continue
            scanned += 1
            _scan_dataset(ds, rel, findings)
    return findings, scanned, unreadable, nonstandard


def report(findings, scanned, unreadable=(), nonstandard=(), out=None, allow=None):
    """Print counts per tag (NEVER values). -> (reject_count, warn_count).
    Unparseable files count toward reject_count: unjudgeable is not clean."""
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
    for rel in sorted(unreadable):
        print(f"PHI REFUSED: unparseable file, plain and force reads both failed, e.g. {rel}", file=out)
    for rel in sorted(nonstandard):
        print(f"PHI REFUSED: malformed file, only force-parsing read it (guesswork cannot attest anonymity), e.g. {rel}", file=out)
    print(
        f"phi-gate: judged {scanned} file(s), unparseable {len(unreadable)}, malformed {len(nonstandard)}; "
        f"refused tags: {len(reject)}; noted tags: {len(warned)}",
        file=out,
    )
    return len(reject) + len(unreadable) + len(nonstandard), len(warned)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir")
    ap.add_argument("--exclude-file", help="file of DIR-relative paths to skip (the caller's upload skip-list)")
    ap.add_argument("--allow-tag", action="append", default=[],
                    help="TAG=REASON: tolerate a reject-tier tag; reason REQUIRED and echoed (auditable)")
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
            tier = "warn-tier (presence alone never refuses)" if tag in WARN_TAGS else "not tracked at all"
            print(f"ERROR: --allow-tag only applies to reject-tier tags; {tag} is {tier}", file=sys.stderr)
            return 2
        allow[tag] = reason.strip()
    exclude = ()
    if a.exclude_file:
        if not os.path.isfile(a.exclude_file):
            print("ERROR: --exclude-file does not exist", file=sys.stderr)
            return 2
        with open(a.exclude_file, encoding="utf-8") as fh:
            exclude = [line.strip() for line in fh if line.strip()]
    findings, scanned, unreadable, nonstandard = scan_dir(a.dir, exclude)
    if scanned == 0 and not unreadable and not nonstandard:
        print("ERROR: no .dcm files under DIR", file=sys.stderr)
        return 2
    reject, _warned = report(findings, scanned, unreadable, nonstandard, allow=allow)
    if reject:
        print("phi-gate: REFUSED. Anonymisation runs upstream of ingestion; fix the source "
              "or --allow-tag TAG=REASON for genuinely public data.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
