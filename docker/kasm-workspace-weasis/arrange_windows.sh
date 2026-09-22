#!/usr/bin/env bash
# Window arrangement for the grading panel (issue #5 gap fix,
# grading_panel_window.py): Weasis's own window, by default, opens wide
# enough to sit on top of the grading panel window, forcing a manual
# resize every session before the panel is even visible -- confirmed via
# a real click-through test, not assumed. wmctrl is already present in
# this base image (confirmed via `apt-cache policy` before assuming it
# needed installing), so this resizes Weasis's window to leave room for
# the panel instead, rather than requiring that manual step.
#
# A PERSISTENT loop, not one-shot (an earlier version was, and got caught
# out by a real session): KasmVNC starts at a fixed default geometry and
# only resizes to the client's real browser-viewport size *after* a real
# client connects -- confirmed via a live session's own VNC log ("Got
# request for framebuffer resize to 1920x950" logged well after this
# script's own first pass would have already run). The exact same root
# cause as the watermark overlay bug fixed in overlay.py. A one-shot
# script measuring xrandr once would tile both windows against that
# earlier, smaller geometry and never notice the later resize. Re-applying
# unconditionally every few seconds for the life of the session (like
# watchdog.sh) means it self-corrects the next time this loop runs,
# without needing to detect the resize event itself. wmctrl calls are
# cheap and idempotent -- reapplying the same geometry repeatedly is a
# harmless no-op, not a visible flicker.
set -uo pipefail  # not -e: a miss here shouldn't take down custom_startup.sh's other background jobs

PANEL_WIDTH="${GRADING_PANEL_WIDTH:-420}"
POLL_INTERVAL_S=2
MAX_TRIES=40   # ~20s at 0.5s each -- generous relative to Weasis's own observed startup time

i=0
while [ "$i" -lt "$MAX_TRIES" ]; do
    if wmctrl -l 2>/dev/null | grep -qi "Weasis"; then
        break
    fi
    sleep 0.5
    i=$((i + 1))
done

if [ "$i" -ge "$MAX_TRIES" ]; then
    echo "arrange_windows.sh: gave up waiting for Weasis's window to appear" >&2
    exit 0
fi

while true; do
    # Parsed from xrandr's own "Screen 0: ... current WIDTH x HEIGHT, ..."
    # line -- the actual current negotiated KasmVNC geometry, re-read every
    # iteration (not cached) so a later resize is picked up on the next pass.
    screen_line=$(xrandr 2>/dev/null | grep '^Screen 0:')
    screen_w=$(echo "$screen_line" | sed -n 's/.*current \([0-9]\+\) x \([0-9]\+\).*/\1/p')
    screen_h=$(echo "$screen_line" | sed -n 's/.*current \([0-9]\+\) x \([0-9]\+\).*/\2/p')

    if [ -n "$screen_w" ] && [ -n "$screen_h" ]; then
        weasis_w=$((screen_w - PANEL_WIDTH))
        # Weasis's main window opens already maximized
        # (_NET_WM_STATE_MAXIMIZED_HORZ/_VERT, confirmed via `xprop`) --
        # xfwm4 ignores a plain geometry resize request on a maximized
        # window entirely (confirmed the hard way: a "successful", exit-0
        # `wmctrl -r ... -e ...` with no visible effect until this
        # un-maximize call was added first).
        wmctrl -r "Weasis" -b remove,maximized_vert,maximized_horz 2>/dev/null
        wmctrl -r "Weasis" -e 0,0,0,"$weasis_w","$screen_h" 2>/dev/null
        wmctrl -r "IP_CMC Grading Panel" -b remove,maximized_vert,maximized_horz 2>/dev/null
        wmctrl -r "IP_CMC Grading Panel" -e 0,"$weasis_w",0,"$PANEL_WIDTH","$screen_h" 2>/dev/null
    fi

    sleep "$POLL_INTERVAL_S"
done
