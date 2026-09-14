#!/usr/bin/env bats
# Tests for watchdog.sh (the forensic watermark overlay's supervisor,
# issue #16). No real overlay.py/GTK/X11 needed: OVERLAY_SCRIPT (a seam
# this script added specifically for these tests -- see its own comment)
# points at a small stub python script instead of the real overlay, and
# WATCHDOG_LOG redirects its log to an isolated tmp file. Since the script
# under test is a deliberate infinite loop, every test bounds it with
# `timeout` rather than waiting for it to exit on its own.
SCRIPT="$BATS_TEST_DIRNAME/watchdog.sh"

setup() {
  export OVERLAY_SCRIPT="$BATS_TEST_TMPDIR/stub_overlay.py"
  export WATCHDOG_LOG="$BATS_TEST_TMPDIR/watchdog.log"
  export COUNTER_FILE="$BATS_TEST_TMPDIR/invocations.txt"
  export STUB_EXIT_CODE="0"
  : > "$COUNTER_FILE"

  # Records one line per invocation, then exits with $STUB_EXIT_CODE --
  # standing in for overlay.py dying (killed, crashed, X error) so
  # watchdog.sh's own relaunch behavior can be observed without a real
  # display or GTK.
  cat > "$OVERLAY_SCRIPT" <<'EOF'
import os, sys
with open(os.environ["COUNTER_FILE"], "a") as f:
    f.write("x\n")
sys.exit(int(os.environ.get("STUB_EXIT_CODE", "0")))
EOF
}

@test "relaunches the overlay after it exits, more than once" {
  timeout 2.5s bash "$SCRIPT" || true
  count=$(wc -l < "$COUNTER_FILE")
  [ "$count" -ge 2 ]
}

@test "logs the relaunch with the overlay's own exit code" {
  export STUB_EXIT_CODE="42"
  timeout 1.2s bash "$SCRIPT" || true
  grep -q "exited (code 42), relaunching" "$WATCHDOG_LOG"
}

@test "waits roughly a second between relaunches, not busy-looping" {
  timeout 3.5s bash "$SCRIPT" || true
  count=$(wc -l < "$COUNTER_FILE")
  # ~1 relaunch/sec over 3.5s -> ~3-4 expected. A much higher count means
  # the `sleep 1` regressed into a busy loop (real CPU-burn risk in a
  # long-running Kasm session); a much lower count means the loop is
  # stalling far longer than intended.
  [ "$count" -ge 2 ]
  [ "$count" -le 6 ]
}

@test "each relaunch produces its own timestamped log line" {
  timeout 2.5s bash "$SCRIPT" || true
  lines=$(grep -c "watermark overlay exited" "$WATCHDOG_LOG")
  count=$(wc -l < "$COUNTER_FILE")
  # Allow lines to trail count by exactly one: `timeout` can kill the loop
  # right after a relaunch is counted but before its log line is written.
  [ "$lines" -ge $((count - 1)) ]
  [ "$lines" -le "$count" ]
}
