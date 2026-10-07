#!/usr/bin/env bats
# Tests for scripts/ci-gate.py (issue #124): ci-success must be green only when
# every job succeeded -- "skipped" (ancestor failed) must block, with the culprit named.

setup() {
  GATE="${BATS_TEST_DIRNAME}/ci-gate.py"
}

@test "all jobs success -> pass" {
  NEEDS_JSON='{"lint":{"result":"success"},"unit-test":{"result":"success"}}' run python3 "$GATE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"All 2 jobs succeeded"* ]]
}

@test "one failure -> fail and names the job" {
  NEEDS_JSON='{"lint":{"result":"success"},"shell-test":{"result":"failure"}}' run python3 "$GATE"
  [ "$status" -eq 1 ]
  [[ "$output" == *"shell-test: failure"* ]]
}

@test "the PR #121 scenario: failed ancestor makes dependents skipped -> fail, not green" {
  NEEDS_JSON='{"shell-test":{"result":"failure"},"smoke-test":{"result":"skipped"},"log-leak-test":{"result":"skipped"}}' run python3 "$GATE"
  [ "$status" -eq 1 ]
  [[ "$output" == *"shell-test: failure"* ]]
  [[ "$output" == *"smoke-test: skipped"* ]]
}

@test "skipped alone blocks" {
  NEEDS_JSON='{"lint":{"result":"success"},"smoke-test":{"result":"skipped"}}' run python3 "$GATE"
  [ "$status" -eq 1 ]
}

@test "cancelled blocks" {
  NEEDS_JSON='{"lint":{"result":"cancelled"}}' run python3 "$GATE"
  [ "$status" -eq 1 ]
}

@test "missing result blocks" {
  NEEDS_JSON='{"lint":{}}' run python3 "$GATE"
  [ "$status" -eq 1 ]
  [[ "$output" == *"lint: missing"* ]]
}

@test "empty needs refuses to pass" {
  NEEDS_JSON='{}' run python3 "$GATE"
  [ "$status" -eq 2 ]
}

@test "invalid JSON refuses to pass" {
  NEEDS_JSON='not json' run python3 "$GATE"
  [ "$status" -eq 2 ]
}

@test "reads stdin when NEEDS_JSON is unset" {
  run bash -c "unset NEEDS_JSON; echo '{\"lint\":{\"result\":\"success\"}}' | python3 '$GATE'"
  [ "$status" -eq 0 ]
}
