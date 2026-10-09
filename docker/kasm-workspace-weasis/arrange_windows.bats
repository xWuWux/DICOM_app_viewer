#!/usr/bin/env bats
# Tests for arrange_windows.sh's kiosk window-keeping (issue #151): a minimised
# window must come back, the grading panel must be relaunched if closed (but never
# double-launched during startup), and the title-bar buttons must be removed.
# No X11/Weasis/GTK: wmctrl, xprop, xrandr and xfconf-query are small stubs on PATH
# (state in files), the panel launcher is a stub (ARRANGE_PANEL_CMD), and the
# otherwise infinite loop is bounded by the script's own ARRANGE_MAX_ITERATIONS seam.
SCRIPT="$BATS_TEST_DIRNAME/arrange_windows.sh"

setup() {
  export STUB_DIR="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$STUB_DIR"
  export PATH="$STUB_DIR:$PATH"
  export WINDOWS_FILE="$BATS_TEST_TMPDIR/windows.txt"
  export ICONIC_FILE="$BATS_TEST_TMPDIR/iconic.txt"
  export CALLS_FILE="$BATS_TEST_TMPDIR/calls.txt"
  export PANEL_LAUNCHES="$BATS_TEST_TMPDIR/panel_launches.txt"
  export PASS_FILE="$BATS_TEST_TMPDIR/pass.txt"; echo 0 > "$PASS_FILE"
  unset VANISH_PASSES PANEL_GONE_FROM WEASIS_GONE_FROM
  : > "$ICONIC_FILE"; : > "$CALLS_FILE"; : > "$PANEL_LAUNCHES"
  printf '%s\n' \
    '0x01000001  0 host Weasis v4.7.3' \
    '0x00800001  0 host IP_CMC Grading Panel' > "$WINDOWS_FILE"

  cat > "$STUB_DIR/wmctrl" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "-l" ]; then
  # VANISH_PASSES (space-separated pass numbers): the panel is hidden from the window
  # list during exactly those loop passes -- counted by the xrandr stub below, which the
  # script calls once at the start of every pass, so the test needs no timers.
  # PANEL_GONE_FROM / WEASIS_GONE_FROM: that window is gone from the given pass onwards
  # (a closed window), again counted in passes, never in seconds.
  pass=$(cat "$PASS_FILE" 2>/dev/null || echo 0)
  out=$(cat "$WINDOWS_FILE")
  case " ${VANISH_PASSES:-} " in *" $pass "*) out=$(printf '%s\n' "$out" | grep -v "Grading Panel") ;; esac
  if [ -n "${PANEL_GONE_FROM:-}" ] && [ "$pass" -ge "$PANEL_GONE_FROM" ]; then out=$(printf '%s\n' "$out" | grep -v "Grading Panel"); fi
  if [ -n "${WEASIS_GONE_FROM:-}" ] && [ "$pass" -ge "$WEASIS_GONE_FROM" ]; then out=$(printf '%s\n' "$out" | grep -v "Weasis v4"); fi
  printf '%s\n' "$out"
  exit 0
fi
echo "wmctrl $*" >> "$CALLS_FILE"
EOF
  cat > "$STUB_DIR/xprop" <<'EOF'
#!/usr/bin/env bash
# xprop -id <ID> WM_STATE | _NET_FRAME_EXTENTS
id="$2"
if [ "${3:-}" = "_NET_FRAME_EXTENTS" ]; then
  echo "_NET_FRAME_EXTENTS(CARDINAL) = ${FRAME_EXTENTS:-5, 5, 29, 5}"; exit 0
fi
if grep -qx "$id" "$ICONIC_FILE"; then echo "WM_STATE(WM_STATE):"; echo "		window state: Iconic"
else echo "WM_STATE(WM_STATE):"; echo "		window state: Normal"; fi
EOF
  cat > "$STUB_DIR/xrandr" <<'EOF'
#!/usr/bin/env bash
echo $(( $(cat "$PASS_FILE" 2>/dev/null || echo 0) + 1 )) > "$PASS_FILE"
echo "Screen 0: minimum 32 x 32, current 2000 x 800, maximum 32768 x 32768"
EOF
  cat > "$STUB_DIR/xfconf-query" <<'EOF'
#!/usr/bin/env bash
echo "xfconf-query $*" >> "$CALLS_FILE"
EOF
  cat > "$STUB_DIR/panel_stub" <<'EOF'
#!/usr/bin/env bash
echo launched >> "$PANEL_LAUNCHES"
EOF
  cat > "$STUB_DIR/weasis_stub" <<'EOF'
#!/usr/bin/env bash
echo "launched: $*" >> "$WEASIS_LAUNCHES"
EOF
  chmod +x "$STUB_DIR"/*
  export WEASIS_LAUNCHES="$BATS_TEST_TMPDIR/weasis_launches.txt"
  : > "$WEASIS_LAUNCHES"
  export ARRANGE_WEASIS_BIN="$STUB_DIR/weasis_stub"
  export ARRANGE_WEASIS_URI="weasis://?study-uri"
  export ARRANGE_WEASIS_COOLDOWN_S="0"
  export ARRANGE_PANEL_CMD="$STUB_DIR/panel_stub"
  export ARRANGE_POLL_INTERVAL_S="0.2"
  export ARRANGE_MAX_ITERATIONS="6"
  export GRADING_PANEL_WIDTH="400"
}

@test "a minimised Weasis window is brought back" {
  echo "0x01000001" > "$ICONIC_FILE"
  run timeout 20s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "wmctrl -i -a 0x01000001" "$CALLS_FILE"
}

@test "a minimised grading panel is brought back" {
  echo "0x00800001" > "$ICONIC_FILE"
  run timeout 20s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "wmctrl -i -a 0x00800001" "$CALLS_FILE"
}

@test "windows that are not minimised are never activated (no focus stealing)" {
  run timeout 20s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  ! grep -q "wmctrl -i -a" "$CALLS_FILE"
}

@test "title-bar minimise/maximise/shade buttons are removed" {
  run timeout 20s bash "$SCRIPT"
  grep -q 'xfconf-query -c xfwm4 -p /general/button_layout -s |' "$CALLS_FILE"
}

@test "a panel that is closed mid-session is relaunched" {
  export PANEL_GONE_FROM="3"
  export ARRANGE_MAX_ITERATIONS="12"
  run timeout 30s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$PANEL_LAUNCHES")" -ge 1 ]
}

@test "the panel is not relaunched before it has ever appeared (startup race)" {
  sed -i '/Grading Panel/d' "$WINDOWS_FILE"
  export ARRANGE_MAX_ITERATIONS="8"
  run timeout 30s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$PANEL_LAUNCHES")" -eq 0 ]
}

# History (root cause of the original flake, CI runs 37774983212 / 37772985998:
# same SHA, push-run green, PR-run red): this test used to delete the panel from
# WINDOWS_FILE for 0.25 s against a 0.2 s poll interval -- on a loaded runner two
# consecutive checks could both land in the gap and (correctly) trigger the
# relaunch the assertion forbade. VANISH_PASSES below removes timers entirely.
@test "a window that vanishes for a single pass is not treated as closed" {
  export VANISH_PASSES="3"          # missing in pass 3 only, present in passes 1-2 and 4-8
  export ARRANGE_MAX_ITERATIONS="8"
  run timeout 30s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$PANEL_LAUNCHES")" -eq 0 ]
}

@test "a window missing for two consecutive passes IS treated as closed and relaunched" {
  export VANISH_PASSES="3 4"        # deterministic counterpart of the test above
  export ARRANGE_MAX_ITERATIONS="8"
  run timeout 30s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$PANEL_LAUNCHES")" -ge 1 ]
}

@test "a Weasis that was closed is relaunched with the same study URI" {
  export WEASIS_GONE_FROM="3"
  export ARRANGE_MAX_ITERATIONS="12"
  run timeout 30s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "launched: weasis://?study-uri" "$WEASIS_LAUNCHES"
}

@test "Weasis is relaunched without an argument when there was no study URI" {
  export ARRANGE_WEASIS_URI=""
  export WEASIS_GONE_FROM="3"
  export ARRANGE_MAX_ITERATIONS="12"
  run timeout 30s bash "$SCRIPT"
  [ "$(head -1 "$WEASIS_LAUNCHES")" = "launched: " ]
}

@test "Weasis relaunches are rate-limited by the cooldown" {
  export ARRANGE_WEASIS_COOLDOWN_S="1000"
  export WEASIS_GONE_FROM="3"
  export ARRANGE_MAX_ITERATIONS="20"
  run timeout 30s bash "$SCRIPT"
  [ "$(wc -l < "$WEASIS_LAUNCHES")" -eq 1 ]
}

@test "a Weasis that never comes back is retried but only up to the limit" {
  export WEASIS_GONE_FROM="3"
  export ARRANGE_MAX_ITERATIONS="40"
  run timeout 60s bash "$SCRIPT"
  [ "$(wc -l < "$WEASIS_LAUNCHES")" -eq 5 ]
  [[ "$output" == *"relaunch limit reached"* ]]
}

@test "Weasis is not relaunched while its window is simply present" {
  export ARRANGE_MAX_ITERATIONS="8"
  run timeout 30s bash "$SCRIPT"
  [ "$(wc -l < "$WEASIS_LAUNCHES")" -eq 0 ]
}

@test "windows are sized by their OUTER frame: nothing below the screen, no overlap (frame 5,5,29,5 on 2000x800, panel width = client width)" {
  run timeout 20s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  # GRADING_PANEL_WIDTH=400 is the panel's CLIENT width -> outer 410 at x=1590;
  # Weasis outer = 2000-410 = 1590 -> client 1580; both outer height 800 -> client 766
  grep -q "wmctrl -r Weasis -e 0,0,0,1580,766" "$CALLS_FILE"
  grep -q "wmctrl -r IP_CMC Grading Panel -e 0,1590,0,400,766" "$CALLS_FILE"
}

@test "a window without frame information falls back to the plain outer size" {
  export FRAME_EXTENTS=""
  sed -i 's/echo "_NET_FRAME_EXTENTS(CARDINAL) = ${FRAME_EXTENTS:-5, 5, 29, 5}"; exit 0/exit 0/' "$STUB_DIR/xprop"
  run timeout 20s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "wmctrl -r Weasis -e 0,0,0,1600,800" "$CALLS_FILE"
}

# ---- issue #185: Weasis follows the grading-api case pointer ---------------------
# curl is a stub that answers from $CASES_FILE, one response per poll (the last line
# repeats): a JSON text, or FAIL (curl exits 22), or the literal text BAD. The weasis
# stub records every hand-over in $WEASIS_LAUNCHES; WEASIS_STUB_RC makes it fail.
URI_HELPER="$BATS_TEST_DIRNAME/weasis_case_uri.py"
UID_A='{"orthanc_study_uid":"1.2.840.1","stage":"learning"}'
UID_B='{"orthanc_study_uid":"1.2.840.2","stage":"assessment"}'
STARTUP_URI_A=""

follow_setup() {
  export VIEWER_URL="http://viewer:8080/" ORTHANC_URL="http://viewer:8043/" GRADING_TOKEN="secret-token-xyz"
  export WEASIS_URI_HELPER="$URI_HELPER" ARRANGE_CASE_POLL_EVERY=1
  export CASES_FILE="$BATS_TEST_TMPDIR/cases.txt" CURL_COUNT="$BATS_TEST_TMPDIR/curl_count.txt" CURL_ARGV="$BATS_TEST_TMPDIR/curl_argv.txt"
  : > "$CURL_COUNT"; : > "$CURL_ARGV"
  cat > "$STUB_DIR/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "$CURL_ARGV"
cat > /dev/null
echo x >> "$CURL_COUNT"
n=$(wc -l < "$CURL_COUNT")
total=$(wc -l < "$CASES_FILE")
[ "$n" -gt "$total" ] && n=$total
line=$(sed -n "${n}p" "$CASES_FILE")
case "$line" in
  FAIL) exit 22 ;;
  BAD) printf '<html>502</html>' ;;
  *) printf '%s' "$line" ;;
esac
STUB
  cat > "$STUB_DIR/weasis_stub" <<'STUB'
#!/usr/bin/env bash
echo "launched: $*" >> "$WEASIS_LAUNCHES"
exit "${WEASIS_STUB_RC:-0}"
STUB
  chmod +x "$STUB_DIR/curl" "$STUB_DIR/weasis_stub"
  # What custom_startup.sh would have computed for the first study (same helper).
  ARRANGE_WEASIS_URI=$(python3 "$URI_HELPER" uri "$UID_A" "$ORTHANC_URL")
  export ARRANGE_WEASIS_URI
  STARTUP_URI_A="$ARRANGE_WEASIS_URI"
}

launches() { grep -c '^launched:' "$WEASIS_LAUNCHES" || true; }

@test "#185: a changed case sends the new study to Weasis exactly once" {
  follow_setup
  printf '%s\n' "$UID_A" "$UID_B" "$UID_B" "$UID_B" > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(launches)" -eq 1 ]
  expected=$(python3 "$URI_HELPER" uri "$UID_B" "$ORTHANC_URL")
  grep -qxF "launched: $expected" "$WEASIS_LAUNCHES"
  [[ "$output" == *"case changed"*"1.2.840.2"* ]]
}

@test "#185: an unchanged case sends nothing" {
  follow_setup
  printf '%s\n' "$UID_A" > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$(launches)" -eq 0 ]
  [[ "$output" != *"case changed"* ]]
}

@test "#185: API failure keeps the study, logs once, and recovers on the next poll" {
  follow_setup
  printf '%s\n' FAIL FAIL "$UID_B" > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$(launches)" -eq 1 ]
  [ "$(grep -c 'unreachable (' <<<"$output")" -eq 1 ]   # first failure only, not every poll
  [[ "$output" == *"keeping the current study"* ]]
}

@test "#185: a non-JSON response is logged and changes nothing" {
  follow_setup
  printf '%s\n' BAD > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$(launches)" -eq 0 ]
  [[ "$output" == *"not a case"* ]]
}

@test "#185: a complete case does not touch Weasis" {
  follow_setup
  printf '%s\n' '{"complete": true}' > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$(launches)" -eq 0 ]
}

@test "#185: a case with an empty or invalid study UID does not touch Weasis" {
  follow_setup
  printf '%s\n' '{"orthanc_study_uid":""}' '{"orthanc_study_uid":"1.2; rm -rf /"}' '{"stage":"x"}' > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$(launches)" -eq 0 ]
}

@test "#185: a failing hand-over is retried, then given up after MAX_SEND_RETRIES" {
  follow_setup
  export WEASIS_STUB_RC=1 ARRANGE_MAX_ITERATIONS=12
  printf '%s\n' "$UID_B" > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$(launches)" -eq 3 ]
  [[ "$output" == *"retry 1/3"* ]] && [[ "$output" == *"giving up"* ]]
}

@test "#185: with no Weasis window nothing is launched here, but the crash relaunch opens the NEW study" {
  follow_setup
  export WEASIS_GONE_FROM=2 ARRANGE_MAX_ITERATIONS=8
  printf '%s\n' "$UID_A" "$UID_B" > "$CASES_FILE"
  run bash "$SCRIPT"
  [[ "$output" == *"Weasis window not present"* ]]
  # The only launches are the crash relaunches, and they carry the NEW study.
  [ "$(launches)" -ge 1 ]
  grep -qxF "launched: $(python3 "$URI_HELPER" uri "$UID_B" "$ORTHANC_URL")" "$WEASIS_LAUNCHES"
  ! grep -qF "$STARTUP_URI_A" "$WEASIS_LAUNCHES"
}

@test "#185: the token is sent on stdin only: never in curl argv, never in the log" {
  follow_setup
  printf '%s\n' "$UID_B" > "$CASES_FILE"
  run bash "$SCRIPT"
  ! grep -q "secret-token-xyz" "$CURL_ARGV"
  [[ "$output" != *"secret-token-xyz"* ]]
}

@test "#185: without VIEWER_URL/ORTHANC_URL/GRADING_TOKEN the feature is disabled with one log line" {
  follow_setup
  unset GRADING_TOKEN
  printf '%s\n' "$UID_B" > "$CASES_FILE"
  run bash "$SCRIPT"
  [ "$(launches)" -eq 0 ]
  [ ! -s "$CURL_COUNT" ]
  [[ "$output" == *"will NOT follow case changes"* ]]
}
