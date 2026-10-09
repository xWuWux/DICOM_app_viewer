#!/usr/bin/env python3
"""Check (and optionally repair, as COPIES) studies whose images carry more than
one PatientID/PatientName.

Why: Orthanc groups by PatientID first. A study whose images disagree on the
patient is split into several Orthanc patients/studies that SHARE one
StudyInstanceUID and series UIDs, so a viewer sees a duplicated, truncated series
(measured on the first real data: a 335-image series became 320 + 15). The
received data has exactly this fault in 2 of 5 studies (18 images).

  python3 scripts/dicom-patient-consistency.py DIR                 # report; exit 1 if inconsistent
  python3 scripts/dicom-patient-consistency.py DIR --fix-out OUT   # write corrected copies + OUT/skip.txt

Repair rule: every image of a study gets the PatientID/PatientName of that
study's MAJORITY identity -- but only if the odd images are provably the same
acquisition (their SOP instance UIDs and instance numbers do not collide with
the majority's within the series); otherwise it refuses and exits 2, because
then they may be someone else's images. Originals are never modified. Load the
result with:  load-local-studies.sh DIR --skip-list OUT/skip.txt  and
load-local-studies.sh OUT.

Prints COUNTS only, never a patient value, UID or path.
"""

import argparse
import collections
import os
import sys
import warnings

warnings.simplefilter("ignore")  # the received files carry UIDs pydicom calls invalid


def scan(root):
    import pydicom

    studies = collections.defaultdict(list)  # study uid -> [(relpath, identity, series, sop, instance_no)]
    empty_uid = []  # images with no StudyInstanceUID at all (issue #160 review N2)
    for dp, _, files in os.walk(root):
        for f in files:
            if not f.lower().endswith(".dcm"):
                continue
            p = os.path.join(dp, f)
            try:
                ds = pydicom.dcmread(p, stop_before_pixels=True)
            except Exception:
                continue
            ident = (str(ds.get("PatientID", "")), str(ds.get("PatientName", "")))
            uid = str(ds.get("StudyInstanceUID", "")).strip()
            if not uid:
                # An empty UID must NOT become one pseudo-study: majority
                # repair would then relabel images from unrelated studies as
                # if they belonged together. They are counted and refused.
                empty_uid.append(os.path.relpath(p, root))
                continue
            studies[uid].append(
                (os.path.relpath(p, root), ident, str(ds.get("SeriesInstanceUID", "")), str(ds.get("SOPInstanceUID", "")), ds.get("InstanceNumber"))
            )
    return studies, empty_uid


def plan(studies):
    """-> (offenders: list of (relpath, majority identity), refused: int, mixed_studies: int)"""
    offenders, refused, mixed = [], 0, 0
    for items in studies.values():
        idents = collections.Counter(i[1] for i in items)
        if len(idents) < 2:
            continue
        mixed += 1
        ranked = idents.most_common()
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            refused += 1
            continue
        major = ranked[0][0]
        rest = [i for i in items if i[1] == major]
        odd = [i for i in items if i[1] != major]
        sops = {i[3] for i in rest}
        nums = collections.defaultdict(set)
        for i in rest:
            nums[i[2]].add(str(i[4]))
        collide = any(i[3] in sops or str(i[4]) in nums.get(i[2], set()) for i in odd)
        if collide:
            refused += 1
            continue
        offenders += [(i[0], major) for i in odd]
    return offenders, refused, mixed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--fix-out", help="write corrected copies of the odd images here (+ skip.txt)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.dir):
        print("ERROR: directory does not exist", file=sys.stderr)
        return 2
    studies, empty_uid = scan(a.dir)
    if not studies and not empty_uid:
        print("ERROR: no readable .dcm files", file=sys.stderr)
        return 2
    offenders, refused, mixed = plan(studies)
    total = sum(len(v) for v in studies.values()) + len(empty_uid)
    print(f"scanned {total} images in {len(studies)} studies; studies with more than one patient identity: {mixed}; images to correct: {len(offenders)}; studies refused: {refused}")
    if empty_uid:
        print(f"REFUSED: {len(empty_uid)} image(s) carry no StudyInstanceUID and cannot be attributed to any study; not repairing those (issue #160 review N2)", file=sys.stderr)
        return 2
    if refused:
        print("REFUSED: some odd images collide with the study's other images (duplicate instance UID/number), so they may not be the same acquisition; not repairing those", file=sys.stderr)
        return 2
    if not offenders:
        print("OK: every study has a single patient identity")
        return 0
    if not a.fix_out:
        print("INCONSISTENT: use --fix-out DIR to write corrected copies (originals stay untouched)", file=sys.stderr)
        return 1

    import pydicom

    os.makedirs(a.fix_out, exist_ok=True)
    skip = []
    for rel, (pid, pname) in offenders:
        ds = pydicom.dcmread(os.path.join(a.dir, rel))
        ds.PatientID = pid
        ds.PatientName = pname
        dst = os.path.join(a.fix_out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        ds.save_as(dst)
        os.chmod(dst, 0o600)
        skip.append(rel)
    skip_file = os.path.join(a.fix_out, "skip.txt")
    with open(skip_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(skip) + "\n")
    os.chmod(skip_file, 0o600)
    print(f"wrote {len(skip)} corrected copies and skip.txt to the --fix-out directory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
