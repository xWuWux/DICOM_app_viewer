#!/usr/bin/env bats
# Tests for scripts/load-local-studies.sh with a stub curl (no Orthanc, no real data).
SCRIPT="$BATS_TEST_DIRNAME/load-local-studies.sh"

setup() {
  export STUB_DIR="$BATS_TEST_TMPDIR/bin"; mkdir -p "$STUB_DIR"
  export PATH="$STUB_DIR:$PATH"
  export CALLS="$BATS_TEST_TMPDIR/calls.txt"; : > "$CALLS"
  export ORTHANC_PASSWORD="SENTINEL-PASSWORD-xyz"
  export ORTHANC_URL="http://orthanc.test:8042"
  cat > "$STUB_DIR/curl" <<'EOF'
#!/usr/bin/env bash
# record argv, fail when the posted file is named like a "bad" one
printf '%s\n' "$*" >> "$CALLS"
for a in "$@"; do case "$a" in @*bad*) printf '503'; exit 0;; esac; done
printf '200'
exit 0
EOF
  chmod +x "$STUB_DIR/curl"
  D="$BATS_TEST_TMPDIR/data"; mkdir -p "$D/a/b"
  : > "$D/a/b/1.dcm"; : > "$D/a/b/2.DCM"; : > "$D/a/3.dcm"
  : > "$D/a/b/1.dcm:Zone.Identifier"; : > "$D/readme.txt"
}

@test "uploads every .dcm (any case) and nothing else" {
  run bash "$SCRIPT" "$D" --jobs 2
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$CALLS")" -eq 3 ]
  [[ "$output" == *"3 uploaded, 0 failed"* ]]
  ! grep -q "Zone.Identifier\|readme" "$CALLS"
}

@test "exits non-zero and counts failures" {
  : > "$D/a/bad.dcm"
  run bash "$SCRIPT" "$D"
  [ "$status" -ne 0 ]
  [[ "$output" == *"4 uploaded, 1 failed"* ]]
  [[ "$output" == *"failure reason: fail:http503 1"* ]]
}

@test "a transient failure is retried and then counts as uploaded" {
  printf '#!/usr/bin/env bash\nn=$(cat "%s" 2>/dev/null || echo 0); echo $((n+1)) > "%s"\nif [ "$n" -lt 2 ]; then printf 503; else printf 200; fi\nexit 0\n' "$BATS_TEST_TMPDIR/n" "$BATS_TEST_TMPDIR/n" > "$STUB_DIR/curl"
  chmod +x "$STUB_DIR/curl"
  rm -f "$D/a/b/2.DCM" "$D/a/3.dcm"
  run bash "$SCRIPT" "$D" --jobs 1
  [ "$status" -eq 0 ]
  [[ "$output" == *"1 uploaded, 0 failed"* ]]
}

@test "the password is never in curl's argv or in the output" {
  run bash "$SCRIPT" "$D"
  ! grep -q "SENTINEL-PASSWORD" "$CALLS"
  [[ "$output" != *"SENTINEL-PASSWORD"* ]]
  grep -q -- "-K " "$CALLS"
}

@test "file names are never printed" {
  run bash "$SCRIPT" "$D"
  [[ "$output" != *"1.dcm"* && "$output" != *"3.dcm"* ]]
}

@test "refuses a missing directory and an empty one" {
  run bash "$SCRIPT" "$BATS_TEST_TMPDIR/nope"
  [ "$status" -eq 2 ]
  mkdir "$BATS_TEST_TMPDIR/empty"
  run bash "$SCRIPT" "$BATS_TEST_TMPDIR/empty"
  [ "$status" -eq 2 ]
}

@test "rejects a bad --jobs value" {
  run bash "$SCRIPT" "$D" --jobs 0
  [ "$status" -eq 2 ]
}

@test "--skip-list leaves the listed files out (paths relative to the directory)" {
  printf 'a/b/1.dcm\na/3.dcm\n' > "$BATS_TEST_TMPDIR/skip.txt"
  run bash "$SCRIPT" "$D" --skip-list "$BATS_TEST_TMPDIR/skip.txt"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$CALLS")" -eq 1 ]
  [[ "$output" == *"1 uploaded, 0 failed"* ]]
}

@test "a trailing slash on the directory does not break --skip-list" {
  printf 'a/3.dcm\n' > "$BATS_TEST_TMPDIR/skip.txt"
  run bash "$SCRIPT" "$D/" --skip-list "$BATS_TEST_TMPDIR/skip.txt"
  [ "$(wc -l < "$CALLS")" -eq 2 ]
}

@test "a missing --skip-list file is rejected" {
  run bash "$SCRIPT" "$D" --skip-list "$BATS_TEST_TMPDIR/nope.txt"
  [ "$status" -eq 2 ]
}
