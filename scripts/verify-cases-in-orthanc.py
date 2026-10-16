#!/usr/bin/env python3
"""Cross-check a built cases file against what the running Orthanc ACTUALLY
holds (issue #160 review, finding S1).

load-local-studies.sh proves every FILE was accepted; it does not prove that
the StudyInstanceUIDs written into the cases file (by build-cases-file.py,
from the export directory) are the studies Orthanc ended up serving -- a
partially failed load, a mixed source directory or the Orthanc split from
issue #162 would all silently desync the two. This script closes that gap as
a separate, re-runnable check (run it after load-local-studies.sh).

    ./scripts/load-local-studies.sh DOK/DICOM_Images
    ORTHANC_PASSWORD=... python3 scripts/verify-cases-in-orthanc.py local/cases.json

Exit codes: 0 every case's study is present; 1 at least one case is missing;
2 configuration/connection error. Output is COUNTS plus the stage/order_index
code of missing cases only -- never a UID, a title or a report value (the
cases file holds the answer key; see app/cases_file.py's docstring). A study
UID appearing more than once in Orthanc is the split signature from issue
#162 and is reported as a warning count.

Stdlib only, like the other non-test scripts in this directory -- EXCEPT the
--fixtures mode (issue #238 point A), which lazily imports pydicom (the
scripts/tests/requirements-dev.txt pin) to read the committed sample-data set.

  python3 scripts/verify-cases-in-orthanc.py --fixtures sample-data/CT_small.dcm

Fixture mode asks the inverse question of cases mode: not "is every graded
answer present" but "is every file a throwaway stack UPLOADED actually SERVED
back, with grouping intact" (study present exactly once, its series and
instance counts matching the files). It is what smoke-test.sh runs so fixture
coverage can never rot silently. Same output policy: counts and committed
FILE NAMES only, never UIDs.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request


def _get_json(base, auth, path, timeout):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": auth})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _basic_auth(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _study_uid_of(base, auth, study_id, timeout):
    """StudyInstanceUID of one study record.

    Reads GET /studies/{id} -> MainDicomTags (keyword-keyed, plain string).
    NOT /studies/{id}/shared-tags: that endpoint is keyed by HEX tags
    ("0020,000d"), so a keyword lookup there returns empty for EVERY study
    -- the bug that silently made cases mode match 0 against any real
    Orthanc since #160 (its unit tests mocked shared-tags in a shape the
    endpoint never returns). A regression test now pins the keyword-free
    contract by refusing any shared-tags request outright."""
    detail = _get_json(base, auth, f"/studies/{study_id}", timeout)
    value = (detail.get("MainDicomTags") or {}).get("StudyInstanceUID", "")
    if isinstance(value, list):  # defensive: some VRs arrive as lists
        value = str(value[0]) if value else ""
    return str(value).strip()


def fetch_study_uids(base, user, password, timeout=30):
    """StudyInstanceUID of every study record Orthanc serves (duplicates kept)."""
    auth = _basic_auth(user, password)
    study_ids = _get_json(base, auth, "/studies", timeout)
    return [_study_uid_of(base, auth, study_id, timeout) for study_id in study_ids]


def check(cases, orthanc_uids, out=None):
    """-> missing count. Prints counts + stage/order codes only."""
    out = out or sys.stdout  # resolved at CALL time, not def time (capsys-friendly)
    present = set(orthanc_uids)
    duplicates = len(orthanc_uids) - len(present)
    missing = [c for c in cases if c["orthanc_study_uid"] not in present]
    print(
        f"cases: {len(cases)}; orthanc study records: {len(orthanc_uids)} "
        f"({len(present)} distinct StudyInstanceUIDs); matched: {len(cases) - len(missing)}",
        file=out,
    )
    if duplicates:
        print(
            f"WARNING: {duplicates} duplicate study record(s) in Orthanc -- the split signature from issue #162",
            file=out,
        )
    for c in missing:
        print(f"  missing from Orthanc: stage={c['stage']} order_index={c['order_index']}", file=out)
    return len(missing)


def fetch_study_inventory(base, user, password, timeout=30):
    """uid -> {"series": n, "instances": n} for every study Orthanc serves.

    Instance counts come from each series resource (the study-level series
    listing does not promise CountInstances across Orthanc versions)."""
    auth = _basic_auth(user, password)
    inventory = {}
    for study_id in _get_json(base, auth, "/studies", timeout):
        uid = _study_uid_of(base, auth, study_id, timeout)
        # real response shape (verified against 26.9.1): a list of dicts with
        # "ID" and an "Instances" list embedded per entry
        series = _get_json(base, auth, f"/studies/{study_id}/series", timeout)
        instances = 0
        for s in series:
            if isinstance(s, dict) and s.get("Instances") is not None:
                instances += len(s["Instances"])
            else:  # older/other shapes: ask the series resource
                sid = s.get("ID") if isinstance(s, dict) else s
                detail = _get_json(base, auth, f"/series/{sid}", timeout)
                instances += int(detail.get("CountInstances", 0))
        # A second Orthanc record with the SAME StudyInstanceUID is the #162
        # split: never merge it into the first (summed counts could make a
        # broken study look complete), count the records instead.
        entry = inventory.setdefault(uid, {"series": 0, "instances": 0, "records": 0})
        entry["records"] += 1
        entry["series"] += len(series)
        entry["instances"] += instances
    return inventory


def fixture_expectations(paths):
    """-> (expected: uid -> {"files", "instances", "series"}, no_uid: [names])

    Lazily imports pydicom; lazy import + same module as cases mode is
    deliberate: one Orthanc client, one output policy, no second fetcher to
    drift (issue #238 review point 2). Files that fail to parse raise with
    their names (committed, public -- naming them is safe); files readable
    but without a StudyInstanceUID are returned separately so main() can
    fail closed with a message that names the REAL cause instead of
    reporting them as "not served" studies.
    """
    import pydicom

    expected = {}
    no_uid = []
    unreadable = []
    for p in paths:
        try:
            ds = pydicom.dcmread(p, stop_before_pixels=True)
        except Exception:
            unreadable.append(os.path.basename(str(p)))
            continue
        uid = str(ds.get("StudyInstanceUID", "")).strip()
        if not uid:
            no_uid.append(os.path.basename(str(p)))
            continue
        entry = expected.setdefault(uid, {"files": [], "instances": 0, "series": set()})
        entry["files"].append(os.path.basename(str(p)))
        entry["instances"] += 1
        entry["series"].add(str(ds.get("SeriesInstanceUID", "")))
    if unreadable:
        raise ValueError("unreadable fixture files: " + ", ".join(sorted(unreadable)))
    for entry in expected.values():
        entry["series"] = len(entry["series"])
    return expected, no_uid


def check_fixtures(expected, inventory, out=None):
    """-> problem count. Prints COUNTS plus committed file names only."""
    out = out or sys.stdout
    problems = 0
    matched = 0
    for uid, exp in sorted(expected.items()):
        got = inventory.get(uid)
        if got is None:
            print(f"  not served: {', '.join(exp['files'])}", file=out)
            problems += 1
            continue
        if got.get("records", 1) > 1:
            # the #162 split: summed counts could MATCH -- the record count
            # alone must fail the gate.
            names = ", ".join(exp["files"])
            print(f"  WARNING duplicate study records (issue #162 split): {names}", file=out)
            problems += 1
            continue
        if got["series"] != exp["series"] or got["instances"] != exp["instances"]:
            print(
                f"  grouping mismatch: {', '.join(exp['files'])} "
                f"(expected series={exp['series']} instances={exp['instances']}, "
                f"served series={got['series']} instances={got['instances']})",
                file=out,
            )
            problems += 1
            continue
        matched += 1
    records = sum(e.get("records", 1) for e in inventory.values())
    print(
        f"fixture studies: {len(expected)}; served study records: {records}; matched: {matched}",
        file=out,
    )
    return problems


def main(argv=None) -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if not os.environ.get("ORTHANC_PASSWORD") and not os.environ.get("ORTHANC_PASS"):
        env_file = os.path.join(repo_root, ".env")
        if os.path.isfile(env_file):
            with open(env_file, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("ORTHANC_PASSWORD="):
                        os.environ["ORTHANC_PASSWORD"] = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                        break

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "cases_file",
        nargs="?",
        help="JSON built by scripts/build-cases-file.py (read locally, never printed)",
    )
    ap.add_argument(
        "--fixtures",
        nargs="+",
        metavar="DCM",
        help="committed .dcm paths that must be SERVED studies with matching grouping (issue #238 point A)",
    )
    a = ap.parse_args(argv)
    if bool(a.cases_file) == bool(a.fixtures):
        print("ERROR: exactly one of cases_file OR --fixtures (not both, not neither)", file=sys.stderr)
        return 2
    if a.fixtures:
        missing_files = [f for f in a.fixtures if not os.path.isfile(f)]
        if missing_files:
            joined = ", ".join(missing_files)
            print(f"ERROR: fixture files not found: {joined}", file=sys.stderr)
            return 2

    base = os.environ.get("ORTHANC_URL", "http://localhost:8042")
    user = os.environ.get("ORTHANC_USER", "orthanc")
    password = os.environ.get("ORTHANC_PASS") or os.environ.get("ORTHANC_PASSWORD")
    if not password:
        print("ERROR: no Orthanc password (ORTHANC_PASSWORD env or .env) -- see .env.example", file=sys.stderr)
        return 2
    if a.fixtures:
        # LOCAL files are read FIRST, outside the network try: a corrupt or
        # UID-less fixture must fail with its own message, never masquerade as
        # an Orthanc connection error (issue #238 A review).
        try:
            expected, no_uid = fixture_expectations(a.fixtures)
        except Exception as exc:  # ValueError naming the files; never file CONTENTS
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if no_uid:
            names = ", ".join(sorted(no_uid))
            print(f"ERROR: fixtures with no StudyInstanceUID: {names}", file=sys.stderr)
            return 2
        try:
            inventory = fetch_study_inventory(base, user, password)
        except Exception:  # bare one-line reason: no tracebacks with request URLs in them
            reason = "is Orthanc up, are the credentials right?"
            print(f"ERROR: cannot read study list from {base} ({reason})", file=sys.stderr)
            return 2
        return 1 if check_fixtures(expected, inventory) else 0

    try:
        with open(a.cases_file, encoding="utf-8") as fh:
            cases = json.load(fh)
        assert isinstance(cases, list) and all({"stage", "order_index", "orthanc_study_uid"} <= set(c) for c in cases)
    except Exception:
        # names the fact, never a value: the file holds the answer key
        print("ERROR: cases file is missing or not the build-cases-file.py format", file=sys.stderr)
        return 2
    try:
        uids = fetch_study_uids(base, user, password)
    except Exception:
        reason = "is Orthanc up, are the credentials right?"
        print(f"ERROR: cannot read study list from {base} ({reason})", file=sys.stderr)
        return 2
    return 1 if check(cases, uids) else 0


if __name__ == "__main__":
    sys.exit(main())
