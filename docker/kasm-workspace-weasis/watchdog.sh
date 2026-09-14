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

while true; do
    python3 /opt/watermark/overlay.py >>/tmp/watermark-overlay.log 2>&1
    echo "$(date -u +%FT%TZ) watermark overlay exited (code $?), relaunching in 1s" >>/tmp/watermark-overlay.log
    sleep 1
done
