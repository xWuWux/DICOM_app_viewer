#!/usr/bin/env bash
# One-shot window arrangement for the grading panel (issue #5 gap fix,
# grading_panel_window.py): Weasis's own window, by default, opens wide
# enough to sit on top of the grading panel window, forcing a manual
# resize every session before the panel is even visible -- confirmed via
# a real click-through test, not assumed. wmctrl is already present in
# this base image (confirmed via `apt-cache policy` before assuming it
# needed installing), so this resizes Weasis's window to leave room for
# the panel instead, rather than requiring that manual step.
#
# Not a persistent watchdog like watchdog.sh -- Weasis's main window is
# only created once per session, so a single successful resize is enough.
# Known trade-off, not handled here: if the client viewport resizes later
# (the same KasmVNC behavior that caused the watermark-overlay bug fixed
# in overlay.py), this split can go stale until the next session.
set -uo pipefail  # not -e: a miss here shouldn't take down custom_startup.sh's other background jobs

PANEL_WIDTH="${GRADING_PANEL_WIDTH:-420}"
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

# Parsed from xrandr's own "Screen 0: ... current WIDTH x HEIGHT, ..." line
# -- the actual negotiated KasmVNC geometry, not a guessed/default one
# (same reasoning as overlay.py/grading_panel_window.py reading GDK's own
# screen size rather than hardcoding a resolution).
screen_line=$(xrandr 2>/dev/null | grep '^Screen 0:')
screen_w=$(echo "$screen_line" | sed -n 's/.*current \([0-9]\+\) x \([0-9]\+\).*/\1/p')
screen_h=$(echo "$screen_line" | sed -n 's/.*current \([0-9]\+\) x \([0-9]\+\).*/\2/p')

if [ -z "$screen_w" ] || [ -z "$screen_h" ]; then
    echo "arrange_windows.sh: could not parse screen geometry from xrandr" >&2
    exit 0
fi

weasis_w=$((screen_w - PANEL_WIDTH))
# Weasis's main window opens already maximized (_NET_WM_STATE_MAXIMIZED_HORZ
# + _NET_WM_STATE_MAXIMIZED_VERT, confirmed via `xprop`) -- xfwm4 ignores a
# plain geometry resize request on a maximized window entirely (confirmed
# the hard way: a "successful", exit-0 `wmctrl -r ... -e ...` with no
# visible effect until this un-maximize call was added first).
wmctrl -r "Weasis" -b remove,maximized_vert,maximized_horz
wmctrl -r "Weasis" -e 0,0,0,"$weasis_w","$screen_h"
