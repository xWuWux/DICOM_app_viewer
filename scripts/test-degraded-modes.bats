#!/usr/bin/env bats
# Guard test for scripts/test-degraded-modes.sh (#219 safety item, mirroring
# scripts/test-orthanc-proxy-scope.bats from #214): the script must refuse to
# start while any ipcmc-* container exists and pass cleanly when none do.
# No Docker daemon needed -- docker is stubbed via DOCKER_BIN.

stub_docker() {
  STUB_DIR="$(mktemp -d)"
  cat > "$STUB_DIR/docker" <<STUB
#!/usr/bin/env bash
case "\$1 \$2" in
  "ps -a") echo "$1" ;;
  *) exit 0 ;;
esac
STUB
  chmod +x "$STUB_DIR/docker"
}

teardown() {
  rm -rf "${STUB_DIR:-}" 2>/dev/null || true
}

@test "preflight refuses while an ipcmc-* container exists (exit 2, nothing touched)" {
  stub_docker "ipcmc-orthanc
degraded-test-grading-api-1"
  run env DOCKER_BIN="$STUB_DIR/docker" bash "$BATS_TEST_DIRNAME/test-degraded-modes.sh" --preflight
  [ "$status" -eq 2 ]
  [[ "$output" == *"REFUSING to start"* ]]
  [[ "$output" != *"guard: clear"* ]]
}

@test "preflight passes when no ipcmc-* container exists" {
  stub_docker "some-unrelated-container"
  run env DOCKER_BIN="$STUB_DIR/docker" bash "$BATS_TEST_DIRNAME/test-degraded-modes.sh" --preflight
  [ "$status" -eq 0 ]
  [[ "$output" == *"guard: clear"* ]]
}

@test "preflight passes on an empty docker ps" {
  stub_docker ""
  run env DOCKER_BIN="$STUB_DIR/docker" bash "$BATS_TEST_DIRNAME/test-degraded-modes.sh" --preflight
  [ "$status" -eq 0 ]
  [[ "$output" == *"guard: clear"* ]]
}
