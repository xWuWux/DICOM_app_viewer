#!/usr/bin/env bats
# Tests for custom_startup.sh (the Weasis workspace's launcher, issue #16).
# No real grading-api, Weasis, or Docker needed:
#   - a stub `curl` (prepended onto PATH) stands in for grading-api's
#     /api/case response, controlled via $CURL_RESPONSE_JSON/$CURL_EXIT_CODE
#   - WEASIS_BIN (a seam this script added specifically for these tests --
#     see its own comment) points at a stub that captures its own argv
#     instead of actually launching Weasis
# The real python3 still runs for the actual dicom:rs URI-building logic --
# that's the whole point of this suite: verifying the real percent-encoding
# behavior that took real debugging to get right (see this script's own
# header comment), not mocking it away.
SCRIPT="$BATS_TEST_DIRNAME/custom_startup.sh"

setup() {
  STUB_DIR="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$STUB_DIR"

  cat > "$STUB_DIR/weasis" <<'EOF'
#!/usr/bin/env bash
# `printf '%s\n' "$@"` alone still emits one bare newline even when $@ is
# empty -- that's a real printf quirk, not this test faking "no args" --
# so build the file arg-by-arg instead, genuinely empty when there are none.
: > "$WEASIS_ARGS_FILE"
for arg in "$@"; do printf '%s\n' "$arg" >> "$WEASIS_ARGS_FILE"; done
EOF
  chmod +x "$STUB_DIR/weasis"

  # A stub curl, not the real one -- this is what lets these tests run
  # against grading-api's response *shape* without grading-api existing.
  # Also records its own argv, so a test can confirm the script actually
  # authenticates with ?token=, not the bare ?student_id= it used to send
  # (a real gap README.md flagged: nothing checked student_id belonged to
  # the caller).
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
  export WEASIS_ARGS_FILE="$BATS_TEST_TMPDIR/weasis-args.txt"
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
  # Decode and check content rather than hardcoding the exact percent-
  # encoding -- this is what actually matters (requestType=STUDY ahead of
  # studyUID, the right DICOMweb URL), not the literal escaped bytes.
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
  [ ! -s "$WEASIS_ARGS_FILE" ]  # empty: no argv captured, i.e. launched with none
}

@test "falls back to a plain launch when grading-api is unreachable" {
  export CURL_EXIT_CODE=7  # curl's own exit code for "couldn't connect"
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ ! -s "$WEASIS_ARGS_FILE" ]
}

@test "falls back to a plain launch when the case response has no study UID" {
  export CURL_RESPONSE_JSON='{"complete": false}'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ ! -s "$WEASIS_ARGS_FILE" ]
}

@test "falls back to a plain launch on a malformed (non-JSON) grading-api response" {
  export CURL_RESPONSE_JSON='not json'
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ ! -s "$WEASIS_ARGS_FILE" ]
}
