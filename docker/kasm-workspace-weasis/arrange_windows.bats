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
  : > "$ICONIC_FILE"; : > "$CALLS_FILE"; : > "$PANEL_LAUNCHES"
  printf '%s\n' \
    '0x01000001  0 host Weasis v4.7.3' \
    '0x00800001  0 host IP_CMC Grading Panel' > "$WINDOWS_FILE"

  cat > "$STUB_DIR/wmctrl" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "-l" ]; then cat "$WINDOWS_FILE"; exit 0; fi
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
  ( sleep 0.5; sed -i '/Grading Panel/d' "$WINDOWS_FILE" ) &
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

@test "a window that vanishes for a single pass is not treated as closed" {
  ( sleep 0.3; sed -i '/Grading Panel/d' "$WINDOWS_FILE"; sleep 0.25; echo '0x00800001  0 host IP_CMC Grading Panel' >> "$WINDOWS_FILE" ) &
  export ARRANGE_MAX_ITERATIONS="10"
  run timeout 30s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$PANEL_LAUNCHES")" -eq 0 ]
}

@test "a Weasis that was closed is relaunched with the same study URI" {
  ( sleep 0.5; sed -i '/Weasis v4/d' "$WINDOWS_FILE" ) &
  export ARRANGE_MAX_ITERATIONS="12"
  run timeout 30s bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "launched: weasis://?study-uri" "$WEASIS_LAUNCHES"
}

@test "Weasis is relaunched without an argument when there was no study URI" {
  export ARRANGE_WEASIS_URI=""
  ( sleep 0.5; sed -i '/Weasis v4/d' "$WINDOWS_FILE" ) &
  export ARRANGE_MAX_ITERATIONS="12"
  run timeout 30s bash "$SCRIPT"
  [ "$(head -1 "$WEASIS_LAUNCHES")" = "launched: " ]
}

@test "Weasis relaunches are rate-limited by the cooldown" {
  export ARRANGE_WEASIS_COOLDOWN_S="1000"
  ( sleep 0.5; sed -i '/Weasis v4/d' "$WINDOWS_FILE" ) &
  export ARRANGE_MAX_ITERATIONS="20"
  run timeout 30s bash "$SCRIPT"
  [ "$(wc -l < "$WEASIS_LAUNCHES")" -eq 1 ]
}

@test "a Weasis that never comes back is retried but only up to the limit" {
  ( sleep 0.5; sed -i '/Weasis v4/d' "$WINDOWS_FILE" ) &
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
