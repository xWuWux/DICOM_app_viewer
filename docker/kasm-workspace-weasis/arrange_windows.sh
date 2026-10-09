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
# Kiosk window-keeping (issue #151). Found by manual testing in a live Kasm
# session: there is no taskbar/panel in this session, so a MINIMISED window
# (title-bar button, or Alt+F9 -- which hid BOTH windows at once) could never
# be brought back and the student was left with a black screen, mid-exam,
# with Weasis and the panel still running underneath. This loop, which already
# owns the layout, therefore also keeps the two windows ALIVE and VISIBLE:
#   1. title-bar buttons for minimise/maximise/shade are removed (xfwm4
#      button_layout) -- the layout is fixed by this script, not by the student;
#   2. an iconified (minimised) window is restored on the next pass;
#   3. the grading panel is relaunched if it was closed (it has no watchdog of
#      its own, unlike the watermark) -- only after it has been seen once, so
#      the startup race (panel not mapped yet) can never double-launch it.
# Keyboard shortcuts (Alt+F9/F10/F11) are not removed one by one: restoring on
# every pass covers all of them, including ones nobody thought of.
#   4. a CLOSED Weasis is relaunched with the same study (live test: after
#      File>Exit the session was a black screen for good -- custom_startup.sh
#      exec's Weasis, so nothing was left to start it again, and the student's
#      progress lives server-side, so a relaunch loses nothing). Rate-limited:
#      at most MAX_WEASIS_RELAUNCHES times per session and never twice within
#      WEASIS_COOLDOWN_S, so a Weasis that crashes on start cannot spin.
#   5. FOLLOWS THE CASE POINTER (issue #185). Weasis is started once, on the first
#      case. When the student submits and moves on, the panel (browser-side) shows
#      the next case but nothing told Weasis. So every ARRANGE_CASE_POLL_EVERY-th
#      pass this script fetches /api/case (same endpoint and X-Grading-Token header
#      the panel uses), builds the weasis:// URI with weasis_case_uri.py (the same
#      helper custom_startup.sh used for the first study) and, when it differs from
#      the study Weasis was last given, launches `Weasis <uri>`: weasis:// is
#      single-instance IPC, so the running Weasis opens the new study.
#      Failure handling (each one logged, none fatal, the current study stays):
#        - API unreachable or not JSON: keep the current study, retry on the next poll
#          (logged on the 1st failure and every 10th, so a long outage is not a flood);
#        - complete case / no study UID / invalid UID: nothing to open, change nothing;
#        - the hand-over process exits non-zero: retried, at most MAX_SEND_RETRIES
#          times per study, then logged and given up (the Weasis-crash relaunch above
#          still opens the CURRENT study, because WEASIS_URI is updated first);
#        - Weasis window not present: only remember the new study; the relaunch
#          above starts Weasis on it (never a second, competing instance).
#      The token is passed to curl on stdin (never in argv) and is never logged.
#      Needs VIEWER_URL, ORTHANC_URL and GRADING_TOKEN in the environment; if any
#      is missing the feature is disabled with one log line.
# Deterrence, not prevention, like the watermark.
#
# Test seams (all unset in production): ARRANGE_CASE_POLL_EVERY (passes between
# polls), ARRANGE_CASE_CURL_TIMEOUT_S, WEASIS_URI_HELPER, and: ARRANGE_POLL_INTERVAL_S,
# ARRANGE_MAX_ITERATIONS (0 = run forever), ARRANGE_PANEL_CMD,
# ARRANGE_WEASIS_BIN / ARRANGE_WEASIS_URI (what to relaunch), ARRANGE_WEASIS_COOLDOWN_S.
set -uo pipefail  # not -e: a miss here shouldn't take down custom_startup.sh's other background jobs

PANEL_WIDTH="${GRADING_PANEL_WIDTH:-420}"
POLL_INTERVAL_S="${ARRANGE_POLL_INTERVAL_S:-2}"
MAX_ITERATIONS="${ARRANGE_MAX_ITERATIONS:-0}"
PANEL_TITLE="IP_CMC Grading Panel"
PANEL_CMD="${ARRANGE_PANEL_CMD:-python3 /opt/grading-panel/grading_panel_window.py}"
WEASIS_BIN="${ARRANGE_WEASIS_BIN:-/opt/weasis/bin/Weasis}"
WEASIS_URI="${ARRANGE_WEASIS_URI:-}"
WEASIS_COOLDOWN_S="${ARRANGE_WEASIS_COOLDOWN_S:-20}"
MAX_WEASIS_RELAUNCHES=5

# Issue #185: follow the grading-api case pointer.
CASE_POLL_EVERY="${ARRANGE_CASE_POLL_EVERY:-3}"            # passes between polls (3 x 2 s = ~6 s)
CASE_CURL_TIMEOUT_S="${ARRANGE_CASE_CURL_TIMEOUT_S:-5}"
MAX_SEND_RETRIES=3
WEASIS_URI_HELPER="${WEASIS_URI_HELPER:-/opt/grading-panel/weasis_case_uri.py}"
VIEWER_URL="${VIEWER_URL:-}"
ORTHANC_URL="${ORTHANC_URL:-}"
GRADING_TOKEN="${GRADING_TOKEN:-}"

# First window id whose wmctrl title contains $1 (the title goes in via -v,
# never interpolated into the awk program).
win_id() {
    wmctrl -l 2>/dev/null | awk -v t="$1" 'index($0, t) { print $1; exit }'
}

# Un-minimise (xfwm4: "Iconic") the window titled $1, if there is one and it is.
restore_if_iconic() {
    local id
    id=$(win_id "$1")
    [ -n "$id" ] || return 0
    if xprop -id "$id" WM_STATE 2>/dev/null | grep -q 'Iconic'; then
        wmctrl -i -a "$id" 2>/dev/null
    fi
}

# The window manager's frame around a client: "left right top bottom" in pixels
# (xfwm4 here: 5 5 29 5 -- a 29 px title bar). `wmctrl -e` takes the frame's
# top-left but the CLIENT's size, so a window given the full screen height as its
# client height ends top+bottom px below the screen, and two side-by-side windows
# given the full width overlap by their side borders (seen live: Weasis' lower edge
# 34 px off screen, 10 px of overlap, panel 10 px past the right edge).
frame_extents() {
    local line
    line=$(xprop -id "$1" _NET_FRAME_EXTENTS 2>/dev/null |
        sed -n 's/.*= *\([0-9]*\), *\([0-9]*\), *\([0-9]*\), *\([0-9]*\).*/\1 \2 \3 \4/p')
    echo "${line:-0 0 0 0}"
}

# Place the window titled $1 so its OUTER frame occupies x=$2, y=0, width $3, height
# $4: the client size is the outer size minus that window's own frame extents.
place() {
    local id fl fr ft fb cw ch
    id=$(win_id "$1")
    [ -n "$id" ] || return 0
    read -r fl fr ft fb < <(frame_extents "$id")
    cw=$(($3 - fl - fr))
    ch=$(($4 - ft - fb))
    if [ "$cw" -le 0 ] || [ "$ch" -le 0 ]; then
        return 0
    fi
    wmctrl -r "$1" -b remove,maximized_vert,maximized_horz 2>/dev/null
    wmctrl -r "$1" -e 0,"$2",0,"$cw","$ch" 2>/dev/null
}

# Issue #185: poll /api/case; when the study changes, hand the new weasis:// URI to
# the running Weasis. State lives in globals (this script is one long loop).
case_fetch_failures=0
current_uid=""
send_pid=""
send_uri=""
send_failures=0
follow_enabled=1
if [ -z "$VIEWER_URL" ] || [ -z "$ORTHANC_URL" ] || [ -z "$GRADING_TOKEN" ]; then
    follow_enabled=0
    echo "arrange_windows.sh: VIEWER_URL/ORTHANC_URL/GRADING_TOKEN not all set -- Weasis will NOT follow case changes" >&2
fi

# Start `Weasis <uri>` in the background and remember it so the next poll can
# check how it ended. A second launch normally forwards the URI to the running
# Weasis and exits 0; if Weasis was not running it simply becomes the instance.
send_study() {
    "$WEASIS_BIN" "$1" &
    send_pid=$!
    send_uri="$1"
}

# Check the previous hand-over: a non-zero exit is a failure, retried a few times.
check_previous_send() {
    [ -n "$send_pid" ] || return 0
    kill -0 "$send_pid" 2>/dev/null && return 0   # still running: it IS the Weasis instance
    local rc=0
    wait "$send_pid" 2>/dev/null || rc=$?
    send_pid=""
    if [ "$rc" -eq 0 ]; then
        send_failures=0
        return 0
    fi
    send_failures=$((send_failures + 1))
    if [ "$send_failures" -lt "$MAX_SEND_RETRIES" ]; then
        echo "arrange_windows.sh: handing the new study to Weasis failed (exit ${rc}), retry ${send_failures}/${MAX_SEND_RETRIES}" >&2
        send_study "$send_uri"
    else
        echo "arrange_windows.sh: handing the new study to Weasis failed ${send_failures} times (last exit ${rc}); giving up on this study" >&2
    fi
}

follow_case() {
    [ "$follow_enabled" = "1" ] || return 0
    check_previous_send
    local json uri new_uid
    if ! json=$(curl -fsS -m "$CASE_CURL_TIMEOUT_S" --config - "${VIEWER_URL}api/case" \
        < <(printf 'header = "X-Grading-Token: %s"\n' "$GRADING_TOKEN") 2>/dev/null); then
        case_fetch_failures=$((case_fetch_failures + 1))
        if [ "$case_fetch_failures" -eq 1 ] || [ $((case_fetch_failures % 10)) -eq 0 ]; then
            echo "arrange_windows.sh: grading-api /api/case unreachable (${case_fetch_failures} failures in a row) -- keeping the current study" >&2
        fi
        return 0
    fi
    case_fetch_failures=0
    local rc=0
    uri=$(python3 "$WEASIS_URI_HELPER" uri "$json" "$ORTHANC_URL" 2>/dev/null) || rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "arrange_windows.sh: /api/case returned something that is not a case (helper exit ${rc}) -- keeping the current study" >&2
        return 0
    fi
    # Empty = complete case or no study: leave Weasis alone.
    [ -n "$uri" ] || return 0
    [ "$uri" != "$WEASIS_URI" ] || return 0

    new_uid=$(python3 "$WEASIS_URI_HELPER" uid "$json" 2>/dev/null)
    echo "arrange_windows.sh: case changed -- Weasis now shows study ${new_uid} (was: ${current_uid:-the study from startup})"
    current_uid="$new_uid"
    WEASIS_URI="$uri"   # also what the crash relaunch below will open
    send_failures=0
    if [ -n "$(win_id "Weasis")" ]; then
        send_study "$uri"
    else
        echo "arrange_windows.sh: Weasis window not present -- the relaunch logic will open study ${new_uid}"
    fi
}

# No title-bar minimise/maximise/shade buttons (best effort).
xfconf-query -c xfwm4 -p /general/button_layout -s "|" 2>/dev/null || true

panel_seen=0
panel_missing=0
weasis_seen=0
weasis_missing=0
weasis_relaunches=0
weasis_last_launch=-1000000
iteration=0
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
    iteration=$((iteration + 1))
    # Parsed from xrandr's own "Screen 0: ... current WIDTH x HEIGHT, ..."
    # line -- the actual current negotiated KasmVNC geometry, re-read every
    # iteration (not cached) so a later resize is picked up on the next pass.
    screen_line=$(xrandr 2>/dev/null | grep '^Screen 0:')
    screen_w=$(echo "$screen_line" | sed -n 's/.*current \([0-9]\+\) x \([0-9]\+\).*/\1/p')
    screen_h=$(echo "$screen_line" | sed -n 's/.*current \([0-9]\+\) x \([0-9]\+\).*/\2/p')

    if [ -n "$screen_w" ] && [ -n "$screen_h" ]; then
        # PANEL_WIDTH is the panel's CLIENT width (grading_panel_window.py pins it
        # with a size hint); its frame comes on top, so the panel's OUTER width is
        # PANEL_WIDTH + its side borders and Weasis gets exactly what is left.
        panel_id=$(win_id "$PANEL_TITLE")
        if [ -n "$panel_id" ]; then
            read -r pfl pfr _ _ < <(frame_extents "$panel_id")
        else
            pfl=5
            pfr=5
        fi
        panel_outer=$((PANEL_WIDTH + pfl + pfr))
        weasis_w=$((screen_w - panel_outer))
        # Weasis's main window opens already maximized
        # (_NET_WM_STATE_MAXIMIZED_HORZ/_VERT, confirmed via `xprop`) --
        # xfwm4 ignores a plain geometry resize request on a maximized
        # window entirely (confirmed the hard way: a "successful", exit-0
        # `wmctrl -r ... -e ...` with no visible effect until the un-maximize
        # call that place() makes first).
        place "Weasis" 0 "$weasis_w" "$screen_h"
        place "$PANEL_TITLE" "$weasis_w" "$panel_outer" "$screen_h"
    fi

    restore_if_iconic "Weasis"
    restore_if_iconic "$PANEL_TITLE"

    if [ -n "$(win_id "$PANEL_TITLE")" ]; then
        panel_seen=1
        panel_missing=0
    elif [ "$panel_seen" = "1" ]; then
        panel_missing=$((panel_missing + 1))
        # Two consecutive misses (not one): a window can briefly vanish while
        # it is being re-mapped; a closed one stays gone.
        if [ "$panel_missing" -ge 2 ]; then
            echo "arrange_windows.sh: grading panel window is gone -- relaunching it" >&2
            # shellcheck disable=SC2086  # PANEL_CMD is a command line, split on purpose
            $PANEL_CMD &
            panel_seen=0
            panel_missing=0
        fi
    fi

    if [ -n "$(win_id "Weasis")" ]; then
        weasis_seen=1
        weasis_missing=0
    elif [ "$weasis_seen" = "1" ]; then
        weasis_missing=$((weasis_missing + 1))
        if [ "$weasis_missing" -ge 2 ]; then
            now=$SECONDS
            if [ "$weasis_relaunches" -ge "$MAX_WEASIS_RELAUNCHES" ]; then
                :  # gave up (already logged once below); a human has to look at this session
            elif [ $((now - weasis_last_launch)) -lt "$WEASIS_COOLDOWN_S" ]; then
                :  # too soon after the last relaunch; try again on a later pass
            else
                weasis_relaunches=$((weasis_relaunches + 1))
                weasis_last_launch=$now
                echo "arrange_windows.sh: Weasis window is gone -- relaunching it (${weasis_relaunches}/${MAX_WEASIS_RELAUNCHES})" >&2
                if [ -n "$WEASIS_URI" ]; then
                    "$WEASIS_BIN" "$WEASIS_URI" &
                else
                    "$WEASIS_BIN" &
                fi
                # seen/missing deliberately NOT reset: if the relaunched Weasis never
                # shows a window, a later pass (after the cooldown) tries again,
                # up to MAX_WEASIS_RELAUNCHES.
                if [ "$weasis_relaunches" -ge "$MAX_WEASIS_RELAUNCHES" ]; then
                    echo "arrange_windows.sh: Weasis relaunch limit reached; not relaunching again" >&2
                fi
            fi
        fi
    fi

    if [ $((iteration % CASE_POLL_EVERY)) -eq 0 ]; then
        follow_case
    fi

    if [ "$MAX_ITERATIONS" -gt 0 ] && [ "$iteration" -ge "$MAX_ITERATIONS" ]; then
        break
    fi
    sleep "$POLL_INTERVAL_S"
done
