#!/usr/bin/env bash
# Watchdog for the forensic watermark overlay (issue #6): if the overlay
# process ever dies -- killed, crashed, X error -- relaunch it immediately.
#
# Matches this project's established "deterrence, not prevention" framing
# (see CLAUDE.md's hard rules and README.md's Security note, and overlay.py's
# own header comment): this does not make the overlay literally unkillable
# -- nothing running as an ordinary process in a Linux container can be, to
# its own container's root user. What it does is make killing it pointless:
# it reappears within about a second, every time.
#
# Launched backgrounded from custom_startup.sh, *before* that script execs
# into Weasis -- a backgrounded child survives its parent shell's later
# exec (exec replaces the calling process's image, it doesn't touch already
# -started children), and since Kasm destroys the whole container on
# logout regardless (ephemeral, zero persistence -- see CLAUDE.md), there's
# no risk of this outliving a session even though it isn't the one process
# Kasm's own service monitor tracks (Weasis is, same as before this issue).
#
# Issue #66 (DoD P91: restart counter, rotation, backoff): the original
# relaunch-every-1s-forever loop did what deterrence needed but punished a
# broken overlay (crashing on start: missing display, bad dependency) with
# an endless 1 Hz restart loop whose output -- capped only by the
# container's disk -- grew in /tmp forever. Now: the FIRST relaunch after a
# death still happens in 1s (the deterrence promise above is unchanged for
# a one-off kill), consecutive fast crashes back off exponentially up to a
# ceiling, an overlay that survives a while resets the backoff, the log is
# rotated past a size cap, and every line carries the cumulative restart
# count -- the "background worker telemetry" the audit asked this script to
# provide (it is the only log a supervisor produces; keeping it bounded and
# counted is the whole fix).
set -uo pipefail

# OVERLAY_SCRIPT and WATCHDOG_LOG are test-only seams (issue #16's BATS
# suite overrides them with a stub script and an isolated tmp path) --
# unset in production, so they always resolve to the real values below.
# The WATCHDOG_* variables below are the issue #66 knobs: same
# test-seam convention, production never sets them and gets the defaults.
OVERLAY_SCRIPT="${OVERLAY_SCRIPT:-/opt/watermark/overlay.py}"
WATCHDOG_LOG="${WATCHDOG_LOG:-/tmp/watermark-overlay.log}"
WATCHDOG_LOG_MAX_BYTES="${WATCHDOG_LOG_MAX_BYTES:-1048576}"
WATCHDOG_LOG_KEEP_LINES="${WATCHDOG_LOG_KEEP_LINES:-2000}"
WATCHDOG_BACKOFF_MAX="${WATCHDOG_BACKOFF_MAX:-60}"
WATCHDOG_STABLE_RESET_SECONDS="${WATCHDOG_STABLE_RESET_SECONDS:-60}"

rotate_log() {
  # Bounded by construction: called between overlay invocations only, so
  # nothing holds the log's fd while we rewrite it (the overlay is dead by
  # the time we get here -- that's why we are looping at all). Checking
  # BEFORE the run rather than after bounds the overshoot to one
  # invocation's output. mv (not in-place truncation) keeps this atomic
  # against a concurrent reader (a student/operator tailing the log).
  # No log yet (first iteration ever) -> nothing to rotate. Checked
  # explicitly: `wc -c < missing` makes the SHELL report the redirect
  # failure on the real stderr before any 2> redirection of the command
  # could suppress it -- a test caught that leak.
  [ -f "$WATCHDOG_LOG" ] || return 0
  local size
  size=$(wc -c < "$WATCHDOG_LOG")
  if [ "$size" -gt "$WATCHDOG_LOG_MAX_BYTES" ]; then
    # tail -n caps how FAR BACK we keep, tail -c caps how BIG the kept
    # part is -- without the second one, a few fat overlay-stdout lines
    # could keep the rotated file above the cap all by itself (a test with
    # a 200-byte cap caught exactly that). tail -c may split a line; it
    # only ever splits the OLDEST kept line, and only when the tail -n
    # window already exceeds the cap (realistic production numbers,
    # 2000 lines vs 1 MB, make it a non-event).
    if tail -n "$WATCHDOG_LOG_KEEP_LINES" "$WATCHDOG_LOG" \
       | tail -c "$WATCHDOG_LOG_MAX_BYTES" > "$WATCHDOG_LOG.rotating" 2>/dev/null; then
      echo "$(date -u +%FT%TZ) log rotated (was $size bytes, cap $WATCHDOG_LOG_MAX_BYTES)" >> "$WATCHDOG_LOG.rotating"
      mv "$WATCHDOG_LOG.rotating" "$WATCHDOG_LOG"
    fi
  fi
}

backoff=1
restarts=0
while true; do
    rotate_log
    started=$SECONDS
    python3 "$OVERLAY_SCRIPT" >>"$WATCHDOG_LOG" 2>&1
    # Captured into a variable *immediately* -- a real bug found while
    # writing this script's BATS tests (issue #16): the exit code the log
    # line used to print was always 0, because $(date ...) below runs its
    # own command (always succeeding) and overwrites $? before the later
    # "$?" in that same echo's string gets expanded, clobbering python3's
    # actual exit status before it was ever read.
    exit_code=$?
    uptime=$(( SECONDS - started ))
    restarts=$(( restarts + 1 ))
    # An overlay that STAYED up proves the loop is healthy again, so the
    # next relaunch goes back to the 1s deterrence cadence. A run that died
    # fast counts as another consecutive failure and doubles the wait --
    # doubling happens AFTER logging/sleeping so the first relaunch of any
    # streak is still 1s (the one-off-kill deterrence promise unchanged).
    if [ "$uptime" -ge "$WATCHDOG_STABLE_RESET_SECONDS" ]; then
        backoff=1
    fi
    echo "$(date -u +%FT%TZ) watermark overlay exited (code $exit_code), relaunching in ${backoff}s (uptime ${uptime}s, ${restarts} restart(s) total)" >>"$WATCHDOG_LOG"
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    if [ "$backoff" -gt "$WATCHDOG_BACKOFF_MAX" ]; then
        backoff="$WATCHDOG_BACKOFF_MAX"
    fi
done
