#!/usr/bin/env python3
"""Final CI gate (issues #124, #135): decides whether `ci-success` may be green.

Reads the `needs` context as JSON (GitHub's `toJSON(needs)`) from the NEEDS_JSON
environment variable, or from stdin if that is unset. Exit 0 only when EVERY listed
job finished with result "success" AND the `needs` list matches the real job set of
the workflow file.

Why not just "no failure": a job whose ancestor failed is reported as "skipped", not
"failure". The old check (`contains(needs.*.result, 'failure')`) therefore stayed green
while an earlier stage was red (seen on PR #121: shell-test failed, stage-5 jobs were
skipped, ci-success was green). Here anything other than "success" -- failure,
cancelled, skipped, or a missing result -- blocks, and the offending jobs are named.

Issue #135 adds two hardenings on top of #124:

(a) Drift guard: the keys of NEEDS_JSON are compared against the job ids parsed from
    the workflow file (default `.github/workflows/ci.yml`, overridable with
    `--workflow PATH`). Jobs present in the workflow but absent from `needs` -- or the
    other way round -- mean the gate no longer covers the pipeline it claims to
    cover: exit 2 with the names from both sides. `ci-success` itself is excluded
    from the comparison. Parsing is pure-stdlib (regex over the `jobs:` block); no
    PyYAML dependency, because the stdlib path is fully sufficient for the narrow
    job-id extraction we need and keeps the gate runnable on any runner unchanged.

(b) Robust input types: a `needs` entry whose value is not an object (e.g.
    `{"lint": "success"}`) reports a clean `<job>: invalid-entry` line and blocks --
    no traceback; `{"job": {"result": null}}` reports `missing` like an absent key.

Fail-closed principle: any unknown shape of input -- invalid JSON, empty needs,
unparseable workflow file, missing workflow file, drift, non-success results --
exits non-zero. The gate can only ever be silent-green when everything checked out.

Runnable locally:
    NEEDS_JSON='{"lint":{"result":"success"}}' python3 scripts/ci-gate.py
    NEEDS_JSON='{"lint":{"result":"success"}}' python3 scripts/ci-gate.py --workflow path/to/ci.yml
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_WORKFLOW_REL = os.path.join(".github", "workflows", "ci.yml")
GATE_JOB = "ci-success"

# A job id line inside the `jobs:` block: exactly two leading spaces, an identifier,
# a colon, and nothing but an optional comment after it. Anything deeper-indented is
# job content, not a job id.
_JOB_KEY_RE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*(?:#.*)?$")
_JOBS_BLOCK_RE = re.compile(r"^jobs:\s*(?:#.*)?$")
_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z0-9_\"'-]+:")


def workflow_job_ids(text: str):
    """Return the job ids declared in the `jobs:` block of a workflow YAML, or None.

    Deliberately tiny: we scan lines from the `jobs:` key until the next top-level
    key or EOF and collect the two-space-indented keys. Returns None when no
    `jobs:` block is found, which callers must treat as fail-closed.
    """
    jobs = []
    in_jobs = False
    for line in text.splitlines():
        if not in_jobs:
            if _JOBS_BLOCK_RE.match(line):
                in_jobs = True
            continue
        if line.startswith("#"):
            continue
        if _TOP_LEVEL_KEY_RE.match(line):
            break  # next top-level section -- the jobs block ended
        match = _JOB_KEY_RE.match(line)
        if match:
            jobs.append(match.group(1))
    if not in_jobs:
        return None
    return jobs


def resolve_workflow_path(explicit: str):
    """Locate the workflow file: explicit path, then cwd, then repo root of this script."""
    if explicit:
        return Path(explicit)
    candidates = [
        Path.cwd() / DEFAULT_WORKFLOW_REL,
        Path(__file__).resolve().parent.parent / DEFAULT_WORKFLOW_REL,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def job_result(info):
    """Classify one `needs` entry.

    Returns the result string ("success", "failure", ...), "missing" for null-ish
    entries or absent/null results, or "invalid-entry" for values that are not
    objects at all (strings, lists, numbers, booleans).
    """
    if isinstance(info, dict):
        result = info.get("result")
        return "missing" if result is None else result
    if info is None:
        return "missing"
    return "invalid-entry"


def main() -> int:
    parser = argparse.ArgumentParser(description="Final CI gate for ci-success (issues #124, #135)")
    parser.add_argument(
        "--workflow",
        metavar="PATH",
        default=None,
        help="workflow file to compare `needs` against "
        "(default: .github/workflows/ci.yml found via cwd, then this script's repo root)",
    )
    args = parser.parse_args()

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

    # --- (b) per-job results, robust to malformed entries ---------------------
    bad = {}
    for name, info in needs.items():
        result = job_result(info)
        if result != "success":
            bad[name] = result

    # --- (a) drift guard: needs keys vs real workflow job ids ------------------
    drift_missing = drift_extra = None
    workflow_path = resolve_workflow_path(args.workflow)
    if workflow_path is None:
        print(
            f"ci-gate: workflow file not found (looked for {DEFAULT_WORKFLOW_REL} in cwd "
            "and in this script's repo root); refusing to pass.",
            file=sys.stderr,
        )
        return 2
    if not workflow_path.is_file():
        print(f"ci-gate: workflow file not found: {workflow_path}; refusing to pass.", file=sys.stderr)
        return 2
    try:
        workflow_text = workflow_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ci-gate: cannot read {workflow_path}: {exc}; refusing to pass.", file=sys.stderr)
        return 2
    declared = workflow_job_ids(workflow_text)
    if declared is None:
        print(f"ci-gate: no `jobs:` block found in {workflow_path}; refusing to pass.", file=sys.stderr)
        return 2
    declared_set = set(declared) - {GATE_JOB}
    needs_set = set(needs)
    drift_missing = sorted(declared_set - needs_set)
    drift_extra = sorted(needs_set - declared_set)

    if drift_missing or drift_extra:
        print(f"CI `needs` has drifted from the job list in {workflow_path}:")
        if drift_missing:
            print("  - in the workflow but MISSING from `needs` (not gated): " + ", ".join(drift_missing))
        if drift_extra:
            print("  - in `needs` but NOT in the workflow (stale entry): " + ", ".join(drift_extra))
        print("Update the `needs` list of the ci-success job, then re-run.")
    if bad:
        print("CI is NOT green. Jobs that did not succeed:")
        for name, result in sorted(bad.items()):
            print(f"  - {name}: {result}")
        print("A 'skipped' result means an earlier job it depends on failed or was cancelled.")
    if drift_missing or drift_extra:
        return 2
    if bad:
        return 1
    print(f"All {len(needs)} jobs succeeded -- ready for code review and merge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
