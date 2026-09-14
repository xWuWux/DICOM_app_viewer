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
set -uo pipefail

# OVERLAY_SCRIPT and WATCHDOG_LOG are test-only seams (issue #16's BATS
# suite overrides them with a stub script and an isolated tmp path) --
# unset in production, so they always resolve to the real values below.
OVERLAY_SCRIPT="${OVERLAY_SCRIPT:-/opt/watermark/overlay.py}"
WATCHDOG_LOG="${WATCHDOG_LOG:-/tmp/watermark-overlay.log}"

while true; do
    python3 "$OVERLAY_SCRIPT" >>"$WATCHDOG_LOG" 2>&1
    # Captured into a variable *immediately* -- a real bug found while
    # writing this script's BATS tests (issue #16): the exit code the log
    # line used to print was always 0, because $(date ...) below runs its
    # own command (always succeeding) and overwrites $? before the later
    # "$?" in that same echo's string gets expanded, clobbering python3's
    # actual exit status before it was ever read.
    exit_code=$?
    echo "$(date -u +%FT%TZ) watermark overlay exited (code $exit_code), relaunching in 1s" >>"$WATCHDOG_LOG"
    sleep 1
done
