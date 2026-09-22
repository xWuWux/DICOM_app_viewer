#!/usr/bin/env bats
# Tests for custom_startup.sh (the Chrome-based workspace's launcher,
# issue #16). No real browser, Docker, or Kasm session needed: CHROME_BIN
# (a seam this script added specifically for these tests -- see its own
# comment) points at a stub that captures its own argv instead of actually
# launching anything, so the exact command the script builds can be
# asserted against directly.
SCRIPT="$BATS_TEST_DIRNAME/custom_startup.sh"

setup() {
  STUB_DIR="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$STUB_DIR"
  cat > "$STUB_DIR/chrome" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$CHROME_ARGS_FILE"
EOF
  chmod +x "$STUB_DIR/chrome"

  export CHROME_BIN="$STUB_DIR/chrome"
  export CHROME_ARGS_FILE="$BATS_TEST_TMPDIR/chrome-args.txt"
  export VIEWER_URL="http://ipcmc-viewer:8080/"
  export ORTHANC_URL="http://ipcmc-viewer:8043/"
  export GRADING_TOKEN="test-token-abc"
  unset STUDENT_ID SESSION_ID
}

@test "fails fast with a clear message when VIEWER_URL is unset" {
  unset VIEWER_URL
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"VIEWER_URL"* ]]
}

@test "fails fast with a clear message when ORTHANC_URL is unset" {
  unset ORTHANC_URL
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"ORTHANC_URL"* ]]
}

@test "fails fast with a clear message when GRADING_TOKEN is unset" {
  unset GRADING_TOKEN
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"GRADING_TOKEN"* ]]
}

@test "defaults STUDENT_ID to UNKNOWN_STUDENT when unset" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q -- "student_id=UNKNOWN_STUDENT" "$CHROME_ARGS_FILE"
}

@test "defaults SESSION_ID to a non-empty numeric timestamp when unset" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  session_id=$(grep -o -- "session_id=[^&]*" "$CHROME_ARGS_FILE" | cut -d= -f2)
  [ -n "$session_id" ]
  [[ "$session_id" =~ ^[0-9]+$ ]]
}

@test "builds the kiosk URL with student_id, session_id, a percent-encoded orthanc_url, and the grading token" {
  export STUDENT_ID="stu_1"
  export SESSION_ID="sess_1"
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -qF -- "--app=http://ipcmc-viewer:8080/?student_id=stu_1&session_id=sess_1&orthanc_url=http%3A%2F%2Fipcmc-viewer%3A8043%2F&token=test-token-abc" "$CHROME_ARGS_FILE"
}

@test "always launches kiosk/incognito with translate disabled" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -qx -- "--kiosk" "$CHROME_ARGS_FILE"
  grep -qx -- "--incognito" "$CHROME_ARGS_FILE"
  grep -qx -- "--disable-features=Translate" "$CHROME_ARGS_FILE"
}
