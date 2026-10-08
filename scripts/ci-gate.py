#!/usr/bin/env python3
"""Final CI gate (issue #124): decides whether `ci-success` may be green.

Reads the `needs` context as JSON (GitHub's `toJSON(needs)`) from the NEEDS_JSON
environment variable, or from stdin if that is unset. Exit 0 only when EVERY listed
job finished with result "success".

Why not just "no failure": a job whose ancestor failed is reported as "skipped", not
"failure". The old check (`contains(needs.*.result, 'failure')`) therefore stayed green
while an earlier stage was red (seen on PR #121: shell-test failed, stage-5 jobs were
skipped, ci-success was green). Here anything other than "success" -- failure,
cancelled, skipped, or a missing result -- blocks, and the offending jobs are named.

Runnable locally:  NEEDS_JSON='{"lint":{"result":"success"}}' python3 scripts/ci-gate.py
"""

import json
import os
import sys


def main() -> int:
    raw = os.environ.get("NEEDS_JSON")
    if raw is None:
        raw = sys.stdin.read()
    try:
        needs = json.loads(raw)
    except ValueError:
        print("ci-gate: NEEDS_JSON is not valid JSON; refusing to pass.", file=sys.stderr)
        return 2
    if not isinstance(needs, dict) or not needs:
        print("ci-gate: no jobs to check (empty `needs`); refusing to pass.", file=sys.stderr)
        return 2

    bad = {name: (info or {}).get("result", "missing") for name, info in needs.items() if (info or {}).get("result") != "success"}
    if bad:
        print("CI is NOT green. Jobs that did not succeed:")
        for name, result in sorted(bad.items()):
            print(f"  - {name}: {result}")
        print("A 'skipped' result means an earlier job it depends on failed or was cancelled.")
        return 1
    print(f"All {len(needs)} jobs succeeded -- ready for code review and merge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
