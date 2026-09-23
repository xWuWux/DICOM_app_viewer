#!/usr/bin/env bats
# Tests for launch-session.sh (the Guacamole flow's launcher). Same
# stubbing technique as docker/kasm-workspace-weasis/custom_startup.bats
# -- no real grading-api, Weasis, or browser needed: WEASIS_BIN/
# BROWSER_BIN (test-only seams this script added specifically for this,
# unset in production) point at stubs that capture their own argv
# instead of actually launching anything, and a stub `curl` stands in for
# grading-api's /api/case response. The real python3 still runs for the
# actual dicom:rs URI-building and URL-encoding logic -- see this script's
# own header comment for why that behavior matters and isn't mocked away.
SCRIPT="$BATS_TEST_DIRNAME/launch-session.sh"

setup() {
  STUB_DIR="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$STUB_DIR"

  cat > "$STUB_DIR/weasis" <<'EOF'
#!/usr/bin/env bash
: > "$WEASIS_ARGS_FILE"
for arg in "$@"; do printf '%s\n' "$arg" >> "$WEASIS_ARGS_FILE"; done
EOF
  chmod +x "$STUB_DIR/weasis"

  cat > "$STUB_DIR/browser" <<'EOF'
#!/usr/bin/env bash
: > "$BROWSER_ARGS_FILE"
for arg in "$@"; do printf '%s\n' "$arg" >> "$BROWSER_ARGS_FILE"; done
EOF
  chmod +x "$STUB_DIR/browser"

  cat > "$STUB_DIR/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$CURL_ARGS_FILE"
if [ -n "${CURL_EXIT_CODE:-}" ] && [ "$CURL_EXIT_CODE" != "0" ]; then
  exit "$CURL_EXIT_CODE"
fi
printf '%s' "${CURL_RESPONSE_JSON:-{\}}"
EOF
  chmod +x "$STUB_DIR/curl"

  export PATH="$STUB_DIR:$PATH"
  export WEASIS_BIN="$STUB_DIR/weasis"
  export BROWSER_BIN="$STUB_DIR/browser"
  export WEASIS_ARGS_FILE="$BATS_TEST_TMPDIR/weasis-args.txt"
  export BROWSER_ARGS_FILE="$BATS_TEST_TMPDIR/browser-args.txt"
  export CURL_ARGS_FILE="$BATS_TEST_TMPDIR/curl-args.txt"
  export VIEWER_URL="http://ipcmc-viewer:8080/"
  export ORTHANC_URL="http://ipcmc-viewer:8043/"
  export GRADING_TOKEN="test-token-abc"
  unset STUDENT_ID SESSION_ID CURL_EXIT_CODE CURL_RESPONSE_JSON
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

@test "authenticates the case lookup with the grading token, not a bare student_id" {
  export CURL_RESPONSE_JSON='{"complete": true}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -qF -- "api/case?token=test-token-abc" "$CURL_ARGS_FILE"
  ! grep -q -- "student_id=" "$CURL_ARGS_FILE"
}

@test "launches the assigned study with a correctly built dicom:rs URI" {
  export CURL_RESPONSE_JSON='{"complete": false, "orthanc_study_uid": "1.2.3.4"}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  uri=$(cat "$WEASIS_ARGS_FILE")
  [[ "$uri" == weasis://\?* ]]
  decoded=$(python3 -c "
import sys, urllib.parse
uri = sys.argv[1].removeprefix('weasis://?')
print(' '.join(urllib.parse.unquote(p) for p in uri.split('+')))
" "$uri")
  [[ "$decoded" == *'$dicom:rs'* ]]
  [[ "$decoded" == *'--url "http://ipcmc-viewer:8043/dicom-web"'* ]]
  [[ "$decoded" == *'requestType=STUDY&studyUID=1.2.3.4'* ]]
}

@test "falls back to a plain launch (no args) when the student has no case assigned" {
  export CURL_RESPONSE_JSON='{"complete": true}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ ! -s "$WEASIS_ARGS_FILE" ]
}

@test "falls back to a plain launch when grading-api is unreachable" {
  export CURL_EXIT_CODE=7
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ ! -s "$WEASIS_ARGS_FILE" ]
}

@test "opens the grading panel with student_id/session_id/token, correctly percent-encoded" {
  export STUDENT_ID="stu 1"  # deliberately needs encoding (space)
  export SESSION_ID="sess_1"
  export CURL_RESPONSE_JSON='{"complete": true}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  # The browser is launched backgrounded (before the script's own final
  # exec into the weasis stub), so its stub's file write races the
  # foreground process rather than being strictly ordered before `run`
  # returns -- poll briefly instead of assuming it's already there.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -s "$BROWSER_ARGS_FILE" ] && break
    sleep 0.1
  done
  url=$(cat "$BROWSER_ARGS_FILE")
  [[ "$url" == "http://ipcmc-viewer:8080/grading-panel.html?student_id=stu%201&session_id=sess_1&token=test-token-abc" ]]
}
