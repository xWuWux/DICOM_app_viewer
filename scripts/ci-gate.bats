#!/usr/bin/env bats
# Tests for scripts/ci-gate.py (issues #124, #135): ci-success must be green only when
# every job succeeded -- "skipped" (ancestor failed) must block, with the culprit named --
# and when the `needs` list still matches the job ids declared in the workflow file.

setup() {
  GATE="${BATS_TEST_DIRNAME}/ci-gate.py"
  WF="$BATS_TEST_TMPDIR/ci.yml"
}

# make_workflow <job>...: write a minimal workflow fixture whose `jobs:` block
# contains exactly the given job ids (plus a ci-success gate job, which the
# drift guard must ignore).
make_workflow() {
  {
    echo "name: fixture"
    echo ""
    echo "on: pull_request"
    echo ""
    echo "jobs:"
    for job in "$@"; do
      echo "  ${job}:"
      echo "    runs-on: ubuntu-latest"
      echo "    steps:"
      echo "      - run: true"
    done
    echo "  # the gate itself must never be counted as needing coverage"
    echo "  ci-success:"
    echo "    runs-on: ubuntu-latest"
  } > "$WF"
}

@test "all jobs success -> pass" {
  make_workflow lint unit-test
  NEEDS_JSON='{"lint":{"result":"success"},"unit-test":{"result":"success"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 0 ]
  [[ "$output" == *"All 2 jobs succeeded"* ]]
}

@test "one failure -> fail and names the job" {
  make_workflow lint shell-test
  NEEDS_JSON='{"lint":{"result":"success"},"shell-test":{"result":"failure"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
  [[ "$output" == *"shell-test: failure"* ]]
}

@test "the PR #121 scenario: failed ancestor makes dependents skipped -> fail, not green" {
  make_workflow shell-test smoke-test log-leak-test
  NEEDS_JSON='{"shell-test":{"result":"failure"},"smoke-test":{"result":"skipped"},"log-leak-test":{"result":"skipped"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
  [[ "$output" == *"shell-test: failure"* ]]
  [[ "$output" == *"smoke-test: skipped"* ]]
}

@test "skipped alone blocks" {
  make_workflow lint smoke-test
  NEEDS_JSON='{"lint":{"result":"success"},"smoke-test":{"result":"skipped"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
}

@test "cancelled blocks" {
  make_workflow lint
  NEEDS_JSON='{"lint":{"result":"cancelled"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
}

@test "missing result blocks" {
  make_workflow lint
  NEEDS_JSON='{"lint":{}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
  [[ "$output" == *"lint: missing"* ]]
}

@test "empty needs refuses to pass" {
  make_workflow lint
  NEEDS_JSON='{}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 2 ]
}

@test "invalid JSON refuses to pass" {
  make_workflow lint
  NEEDS_JSON='not json' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 2 ]
}

@test "reads stdin when NEEDS_JSON is unset" {
  make_workflow lint
  run bash -c "unset NEEDS_JSON; echo '{\"lint\":{\"result\":\"success\"}}' | python3 '$GATE' --workflow '$WF'"
  [ "$status" -eq 0 ]
}

# ---- issue #135 (a): drift guard between NEEDS_JSON and the workflow file ----

@test "drift: job present in workflow but missing from needs -> 2 and names it" {
  make_workflow lint new-job
  NEEDS_JSON='{"lint":{"result":"success"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 2 ]
  [[ "$output" == *"MISSING from \`needs\`"* ]]
  [[ "$output" == *"new-job"* ]]
}

@test "drift: stale extra job in needs (removed from workflow) -> 2 and names it" {
  make_workflow lint
  NEEDS_JSON='{"lint":{"result":"success"},"removed-job":{"result":"success"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 2 ]
  [[ "$output" == *"NOT in the workflow"* ]]
  [[ "$output" == *"removed-job"* ]]
}

@test "drift: names from both sides are reported together" {
  make_workflow lint added-job
  NEEDS_JSON='{"lint":{"result":"success"},"gone-job":{"result":"success"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 2 ]
  [[ "$output" == *"added-job"* ]]
  [[ "$output" == *"gone-job"* ]]
}

@test "drift guard outranks a plain failure (exit 2, but failure still printed)" {
  make_workflow lint extra-job
  NEEDS_JSON='{"lint":{"result":"failure"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 2 ]
  [[ "$output" == *"lint: failure"* ]]
  [[ "$output" == *"extra-job"* ]]
}

@test "workflow file without a jobs: block refuses to pass" {
  printf 'name: broken\non: push\n' > "$WF"
  NEEDS_JSON='{"lint":{"result":"success"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 2 ]
  [[ "$output" == *"no \`jobs:\` block"* ]]
}

@test "workflow file that does not exist refuses to pass" {
  NEEDS_JSON='{"lint":{"result":"success"}}' run python3 "$GATE" --workflow "$BATS_TEST_TMPDIR/nope.yml"
  [ "$status" -eq 2 ]
  [[ "$output" == *"workflow file not found"* ]]
}

@test "drift guard ignores the ci-success job itself" {
  make_workflow lint unit-test
  # make_workflow already appends ci-success to the fixture; needs must NOT list it.
  NEEDS_JSON='{"lint":{"result":"success"},"unit-test":{"result":"success"}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 0 ]
}

# ---- issue #135 (b): malformed entry values block cleanly, no traceback ------

@test "entry value is a string -> 1 with invalid-entry, no traceback" {
  make_workflow lint
  NEEDS_JSON='{"lint":"success"}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
  [[ "$output" == *"lint: invalid-entry"* ]]
  [[ "$output" != *"Traceback"* ]]
}

@test "entry value is a list -> 1 with invalid-entry" {
  make_workflow lint
  NEEDS_JSON='{"lint":["success"]}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
  [[ "$output" == *"lint: invalid-entry"* ]]
  [[ "$output" != *"Traceback"* ]]
}

@test "result null is reported as missing, not None" {
  make_workflow lint
  NEEDS_JSON='{"lint":{"result":null}}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
  [[ "$output" == *"lint: missing"* ]]
  [[ "$output" != *"None"* ]]
}

@test "null entry reports missing and stays clean" {
  make_workflow lint
  NEEDS_JSON='{"lint":null}' run python3 "$GATE" --workflow "$WF"
  [ "$status" -eq 1 ]
  [[ "$output" == *"lint: missing"* ]]
  [[ "$output" != *"Traceback"* ]]
}
