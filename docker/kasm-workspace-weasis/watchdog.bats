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

  # Records one line per invocation, echoes one stdout line (exercises the
  # overlay-output-goes-into-the-log path), then exits with STUB_EXIT_CODE
  # -- standing in for overlay.py dying (killed, crashed, X error) so
  # watchdog.sh's own relaunch behavior can be observed without a real
  # display or GTK. STABLE_AFTER_INVOCATIONS (issue #66 tests): the first
  # N invocations die immediately like a crash-looping overlay, every
  # later one stays up STABLE_RUN_SECONDS first like a healthy restart.
  cat > "$OVERLAY_SCRIPT" <<'EOF'
import os, sys, time
with open(os.environ["COUNTER_FILE"], "a") as f:
    f.write("x\n")
    f.flush()
n = sum(1 for _ in open(os.environ["COUNTER_FILE"]))
after = int(os.environ.get("STABLE_AFTER_INVOCATIONS", "0"))
if after and n > after:
    time.sleep(float(os.environ.get("STABLE_RUN_SECONDS", "1.2")))
print("overlay stdout " + "x" * 60)
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

# ---- issue #66: backoff, reset, rotation, restart counter ----

@test "crash loop backs off exponentially and stops at the ceiling" {
  export WATCHDOG_BACKOFF_MAX=4
  timeout 8s bash "$SCRIPT" || true
  grep -q "relaunching in 1s" "$WATCHDOG_LOG"
  grep -q "relaunching in 2s" "$WATCHDOG_LOG"
  grep -q "relaunching in 4s" "$WATCHDOG_LOG"
  # 4 is the ceiling: an 8s line would mean the doubling ignored the cap.
  ! grep -q "relaunching in 8s" "$WATCHDOG_LOG"
  # 0s/1s/3s/7s launch points inside 8s -> at most 5, and clearly NOT the
  # ~8 launches the old fixed-1s loop would have produced.
  count=$(wc -l < "$COUNTER_FILE")
  [ "$count" -ge 3 ]
  [ "$count" -le 5 ]
}

@test "a stable overlay run resets the backoff to 1s" {
  export WATCHDOG_BACKOFF_MAX=8
  export WATCHDOG_STABLE_RESET_SECONDS=1
  export STABLE_AFTER_INVOCATIONS=2   # first two die fast, then it "works"
  timeout 7s bash "$SCRIPT" || true
  # escalation must have happened first (2s line exists) and the LAST
  # relaunch delay must be back to 1s -- ordering, not mere presence.
  grep -q "relaunching in 2s" "$WATCHDOG_LOG"
  awk '/relaunching in 2s/ { two = NR }
       /relaunching in 1s \(/ { one = NR }
       END { exit !(one && two && one > two) }' "$WATCHDOG_LOG"
}

@test "log rotation keeps the file bounded (issue #66 unbounded /tmp)" {
  export WATCHDOG_LOG_MAX_BYTES=200
  export WATCHDOG_LOG_KEEP_LINES=3
  # ceiling 1 keeps the ~1s cadence so the loop actually iterates several
  # times inside the timeout (the default 60s ceiling would sleep past it
  # after the first crash and never test the growth path).
  export WATCHDOG_BACKOFF_MAX=1
  timeout 3s bash "$SCRIPT" || true
  # Invariant: at any moment size <= cap + rotation-marker + one
  # iteration's output (the only checkpoint is BEFORE a run, so one
  # stub line (~75B) + one watchdog line (~95B) can land after the last
  # rotation; the marker itself adds ~55B). 200+170+55 = 425 -> 500 with
  # margin. WITHOUT the fix this scenario is unbounded by construction.
  size=$(wc -c < "$WATCHDOG_LOG")
  [ "$size" -le 500 ]
  grep -q "log rotated" "$WATCHDOG_LOG"
  lines=$(wc -l < "$WATCHDOG_LOG")
  [ "$lines" -le 7 ]
}

@test "log line carries the cumulative restart counter" {
  export STUB_EXIT_CODE=7
  timeout 2.2s bash "$SCRIPT" || true
  # 1s cadence at the start of a streak: t=0 -> "1 restart(s) total",
  # t=1 -> "2 restart(s) total". Both must be present and ordered.
  grep -q "1 restart(s) total" "$WATCHDOG_LOG"
  grep -q "2 restart(s) total" "$WATCHDOG_LOG"
  awk '/1 restart\(s\) total/ { one = NR }
       /2 restart\(s\) total/ { two = NR }
       END { exit !(one && two && one < two) }' "$WATCHDOG_LOG"
}
