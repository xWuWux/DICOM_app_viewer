#!/usr/bin/env python3
"""Build the local cases file for grading-api (GRADING_CASES_FILE) from the
radiologist's spreadsheet and the received DICOM studies.

  python3 scripts/build-cases-file.py \\
      --xlsx Dokumentacja/Opisy_badan/<file>.xlsx \\
      --dicom-dir Dokumentacja/DICOM_Images \\
      --out local/cases.json \\
      --stages learning=all assessment=1 test=1

The spreadsheet's first sheet needs the columns  Anonim | Opis | Lung-Rads
(patient code, reference description, category). Each code is matched to the
DICOM studies by PatientName/PatientID; the StudyInstanceUID is read from the
headers, so the UIDs never have to be typed or copied.

The OUTPUT contains the exam's answer key and clinical reference reports: it is
written with mode 0600 into the git-ignored ./local directory and must never be
committed (the GitHub repo is public). This script prints counts and the output
path ONLY, never a value from either input.

--stages maps spreadsheet rows (1-based, in sheet order) to stages:
  "all", a single number, or a comma list, e.g. learning=all assessment=2 test=1,3
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

STAGES = ["learning", "assessment", "test"]
TITLE_SUFFIX = {"learning": "nauka", "assessment": "ocena", "test": "test"}
CATEGORIES = {"0", "1", "2", "3", "4A", "4B", "4X"}
_CAT_RE = re.compile(r"^(0|1|2|3|4A|4B|4X)(?:\s*[,;/ ]?\s*(S))?$")


def fail(msg: str) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def parse_category(value, row: int):
    """('2', 0) from 2 / 2.0 / '2' / '4a' / '4A S' ... or fail naming only the row."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip().upper() if value is not None else ""
    m = _CAT_RE.match(text)
    if not m:
        fail(f"spreadsheet row {row}: Lung-Rads value is not a known category (0,1,2,3,4A,4B,4X, optional S)")
    return m.group(1), 1 if m.group(2) else 0


def read_rows(xlsx: str):
    try:
        import openpyxl
    except ImportError:
        fail("openpyxl is required (pip install -r scripts/tests/requirements-dev.txt)")
    try:
        wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    except Exception:
        fail("cannot open the spreadsheet (is it a valid .xlsx?)")
    sheet = wb.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        fail("the first sheet of the spreadsheet is empty")
    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
    cols = {}
    for want, key in (("anonim", "code"), ("opis", "report"), ("lung-rads", "cat")):
        if want not in header:
            fail(f"spreadsheet header is missing the column '{want}' (found {len(header)} columns)")
        cols[key] = header.index(want)
    out = []
    for i, r in enumerate(rows[1:], start=2):
        if r is None or all(c is None or str(c).strip() == "" for c in r):
            continue
        code, report, cat = (r[cols[k]] if cols[k] < len(r) else None for k in ("code", "report", "cat"))
        if code is None or str(code).strip() == "":
            fail(f"spreadsheet row {i}: empty Anonim code")
        if report is None or str(report).strip() == "":
            fail(f"spreadsheet row {i}: empty Opis")
        category, modifier = parse_category(cat, i)
        out.append({"row": i, "code": str(code).strip(), "report": str(report).strip(), "cat": category, "mod": modifier})
    if not out:
        fail("the spreadsheet has no data rows")
    return out


def study_uids_by_code(dicom_dir: str) -> dict:
    try:
        import pydicom
    except ImportError:
        fail("pydicom is required (pip install -r scripts/tests/requirements-dev.txt)")
    import warnings

    warnings.simplefilter("ignore")  # the received files carry UIDs pydicom calls invalid (leading dot)
    # Per study: the MAJORITY PatientName / PatientID decides which code the study
    # belongs to. A few mislabelled images (seen in the received data: 18 of 9,155)
    # must not make one spreadsheet code match two studies; the repair of those
    # images is scripts/dicom-patient-consistency.py's job.
    import collections

    names: dict = collections.defaultdict(collections.Counter)
    ids: dict = collections.defaultdict(collections.Counter)
    n = 0
    for dp, _, files in os.walk(dicom_dir):
        for f in files:
            if not f.lower().endswith(".dcm"):
                continue
            n += 1
            try:
                ds = pydicom.dcmread(os.path.join(dp, f), stop_before_pixels=True)
            except Exception:
                continue
            uid = str(ds.get("StudyInstanceUID", "")).strip()
            if not uid:
                continue
            names[uid][str(ds.get("PatientName", "")).strip()] += 1
            ids[uid][str(ds.get("PatientID", "")).strip()] += 1
    if n == 0:
        fail("no .dcm files found in --dicom-dir")
    found: dict = {}
    for uid in names:
        for counter in (names[uid], ids[uid]):
            ranked = counter.most_common()
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                fail("a DICOM study has two equally common patient labels (run scripts/dicom-patient-consistency.py)")
            if ranked and ranked[0][0]:
                found.setdefault(ranked[0][0], set()).add(uid)
    return found


def parse_stage_spec(specs, total: int) -> dict:
    mapping = {}
    for spec in specs:
        stage, _, rhs = spec.partition("=")
        if stage not in STAGES or not rhs:
            fail(f"--stages entry '{spec[:20]}' must look like stage=all|N|N,M with stage in {STAGES}")
        if rhs == "all":
            idx = list(range(1, total + 1))
        else:
            try:
                idx = [int(x) for x in rhs.split(",")]
            except ValueError:
                fail(f"--stages {stage}: values must be 'all' or numbers")
        if any(i < 1 or i > total for i in idx) or len(set(idx)) != len(idx):
            fail(f"--stages {stage}: numbers must be unique and between 1 and {total}")
        mapping[stage] = idx
    for stage in STAGES:
        if stage not in mapping:
            fail(f"--stages must map every stage; missing {stage}")
    return mapping


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--dicom-dir", required=True)
    ap.add_argument("--out", default="local/cases.json")
    ap.add_argument("--stages", nargs="+", default=["learning=all", "assessment=1", "test=1"])
    ap.add_argument("--force", action="store_true", help="overwrite an existing --out file")
    a = ap.parse_args(argv)

    if not Path(a.xlsx).is_file():
        fail("--xlsx does not exist")
    if not Path(a.dicom_dir).is_dir():
        fail("--dicom-dir does not exist")
    out = Path(a.out)
    if out.exists() and not a.force:
        fail("--out already exists (use --force to overwrite)")

    rows = read_rows(a.xlsx)
    by_code = study_uids_by_code(a.dicom_dir)
    for r in rows:
        uids = by_code.get(r["code"], set())
        if len(uids) != 1:
            fail(f"spreadsheet row {r['row']}: its Anonim code matches {len(uids)} DICOM studies (need exactly 1)")
        r["uid"] = next(iter(uids))
    if len({r["uid"] for r in rows}) != len(rows):
        fail("two spreadsheet rows point at the same DICOM study")

    mapping = parse_stage_spec(a.stages, len(rows))
    cases = []
    for stage in STAGES:
        for order, n in enumerate(mapping[stage]):
            r = rows[n - 1]
            cases.append(
                {
                    "stage": stage,
                    "order_index": order,
                    "orthanc_study_uid": r["uid"],
                    "title": f"Przypadek {order + 1} ({TITLE_SUFFIX[stage]})",
                    "ground_truth_category": r["cat"],
                    "ground_truth_modifier_s": r["mod"],
                    "reference_report": r["report"],
                }
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cases, fh, ensure_ascii=False, indent=2)
    os.chmod(out, 0o600)
    per = {s: sum(1 for c in cases if c["stage"] == s) for s in STAGES}
    print(f"wrote {len(cases)} cases to {out} (mode 0600): {per}; {len(rows)} studies matched. Do NOT commit this file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
