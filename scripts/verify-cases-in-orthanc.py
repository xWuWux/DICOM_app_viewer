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

Stdlib only, like the other non-test scripts in this directory.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request


def fetch_study_uids(base, user, password, timeout=30):
    """StudyInstanceUID of every study record Orthanc serves (duplicates kept)."""
    auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def get(path):
        req = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": auth})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    uids = []
    for study_id in get("/studies"):
        tags = get(f"/studies/{study_id}/shared-tags")
        value = tags.get("StudyInstanceUID", {}).get("Value", "")
        if isinstance(value, list):  # multi-valued VRs arrive as lists
            value = str(value[0]) if value else ""
        uids.append(str(value).strip())
    return uids


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
    ap.add_argument("cases_file", help="JSON built by scripts/build-cases-file.py (read locally, never printed)")
    a = ap.parse_args(argv)

    try:
        with open(a.cases_file, encoding="utf-8") as fh:
            cases = json.load(fh)
        assert isinstance(cases, list) and all({"stage", "order_index", "orthanc_study_uid"} <= set(c) for c in cases)
    except Exception:
        # names the fact, never a value: the file holds the answer key
        print("ERROR: cases file is missing or not the build-cases-file.py format", file=sys.stderr)
        return 2

    base = os.environ.get("ORTHANC_URL", "http://localhost:8042")
    user = os.environ.get("ORTHANC_USER", "orthanc")
    password = os.environ.get("ORTHANC_PASS") or os.environ.get("ORTHANC_PASSWORD")
    if not password:
        print("ERROR: no Orthanc password (ORTHANC_PASSWORD env or .env) -- see .env.example", file=sys.stderr)
        return 2
    try:
        uids = fetch_study_uids(base, user, password)
    except Exception:  # a bare, one-line reason: no tracebacks with request URLs in them
        print(f"ERROR: cannot read study list from {base} (is Orthanc up, are the credentials right?)", file=sys.stderr)
        return 2
    return 1 if check(cases, uids) else 0


if __name__ == "__main__":
    sys.exit(main())
