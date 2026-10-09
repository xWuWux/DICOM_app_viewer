#!/usr/bin/env bash
# Guacamole-flow equivalent of docker/kasm-workspace-weasis/custom_startup.sh.
# Hosting-focused per the owner's decision of 2026-10-09: no Weasis
# export/import lockdown and (until issue #180) no window auto-tiling.
# The forensic watermark IS started here since issue #179 (mandatory).
# Fetches the assigned case, launches Weasis on it, plus a plain browser
# tab for the grading panel. Do not point this at
# real patient data; see docker-compose.guacamole.yml's own header
# comment.
#
# The case-fetch + weasis:// URI construction below is copied verbatim
# from docker/kasm-workspace-weasis/custom_startup.sh -- that logic (which
# stage/study to load, the exact dicom:rs invocation) has nothing to do
# with which remote-display technology streams the pixels, so it's
# identical here. See that file's own header comment for the reasoning
# behind the exact invocation (weasis:// URI vs raw tokens, requestType
# ordering, per-token percent-encoding, the Host-header fix for QIDO-RS
# RetrieveURLs) -- not repeated here to avoid the two drifting apart.
set -euo pipefail

STUDENT_ID="${STUDENT_ID:-UNKNOWN_STUDENT}"
SESSION_ID="${SESSION_ID:-$(date +%s)}"
VIEWER_URL="${VIEWER_URL:?set VIEWER_URL to the internal viewer address}"
ORTHANC_URL="${ORTHANC_URL:?set ORTHANC_URL to the internal auth-proxy address}"
GRADING_TOKEN="${GRADING_TOKEN:?set GRADING_TOKEN to the token minted by grading-api POST /session}"
# CR #121 nit: the token is interpolated into a curl --config line wrapped
# in double quotes; a value containing " or \\ could close the quote and
# inject further curl options. grading-api only ever mints token_urlsafe
# (base64url) values, so anything else means corruption or tampering --
# fail fast and loud instead of feeding curl a crafted config.
if [[ ! "$GRADING_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "ERROR: GRADING_TOKEN contains characters outside the URL-safe alphabet ([A-Za-z0-9_-]) -- refusing to launch" >&2
  exit 1
fi

# issue #94: the token rides an X-Grading-Token HEADER, fed to curl via
# --config on stdin (bash process substitution + builtin printf) so it is
# never in curl's argv (`ps`-invisible) and never in a query string
# (nginx logs). Mirrors docker/kasm-workspace-weasis/custom_startup.sh.
# issue #102: same fail-open removal as docker/kasm-workspace-weasis/
# custom_startup.sh -- unreachable grading-api must never masquerade as
# {"complete": true"}. 3 attempts, linear backoff, token-free error line;
# empty CASE_JSON then falls through to the documented plain-launch path.
# API_RETRY_BACKOFF is a test seam (integer seconds; BATS uses 0).
API_ATTEMPTS=3
API_RETRY_BACKOFF="${API_RETRY_BACKOFF:-2}"
CASE_JSON=""
api_ok=0
for attempt in 1 2 3; do
  if CASE_JSON=$(curl -fsS --config - "${VIEWER_URL}api/case" < <(printf 'header = "X-Grading-Token: %s"\n' "$GRADING_TOKEN") 2>/dev/null); then
    api_ok=1
    break
  fi
  if [ "$attempt" -lt "$API_ATTEMPTS" ]; then
    sleep "$((attempt * API_RETRY_BACKOFF))"
  fi
done
if [ "$api_ok" != "1" ]; then
  echo "launch-session: ERROR grading-api unreachable at ${VIEWER_URL} after ${API_ATTEMPTS} attempts -- opening a study-less session; the case was NOT completed, use the panel's retry" >&2
fi

WEASIS_URI=$(python3 - "$CASE_JSON" "$ORTHANC_URL" <<'PYEOF'
import json, sys, urllib.parse

case_json, orthanc_url = sys.argv[1], sys.argv[2]
try:
    case = json.loads(case_json)
except ValueError:
    sys.exit(0)

study_uid = case.get("orthanc_study_uid")
if case.get("complete") or not study_uid:
    sys.exit(0)

dicomweb_url = orthanc_url.rstrip("/") + "/dicom-web"
parts = [
    "$dicom:rs",
    "--url",
    f'"{dicomweb_url}"',
    "-r",
    f'"requestType=STUDY&studyUID={study_uid}"',
]
print("weasis://?" + "+".join(urllib.parse.quote(p, safe="") for p in parts))
PYEOF
)

# Grading panel: a plain browser tab, not the locked-down WebKit2GTK embed
# docker/kasm-workspace-weasis/grading_panel_window.py builds -- that
# component exists specifically to close off save/print/devtools/
# navigation as attack surface, all explicitly out of scope here.
#
# WEBKIT_FORCE_SANDBOX=0: fixes a *different* problem than it looks like
# at first -- confirmed the hard way (issue #52's own investigation) that
# this does NOT disable bubblewrap (bwrap) itself. epiphany's WebProcess
# and its xdg-dbus-proxy still run wrapped in bwrap regardless of this
# variable (visible in `ps aux` inside a real session either way). What
# actually happens without it: WebKitGTK's own sandbox-negotiation layer
# crash-loops the WebProcess repeatedly ("Web process crashed", over and
# over) even when bwrap's own namespace syscalls succeed -- a GTK-level
# issue, unrelated to seccomp. This variable avoids that crash loop; it is
# NOT a substitute for giving bwrap the namespace/mount syscalls it still
# needs regardless -- see scripts/provision-guacamole-session.py's own
# comment and docker/guacamole-weasis/seccomp/build-profile.py for that
# separate, still-necessary fix.
# Issue #179: forensic watermark (mandatory, CLAUDE.md), same as the Kasm
# flow's custom_startup.sh: picom makes the overlay window genuinely
# transparent (without it overlay.py falls back to a glyph-shaped window), and
# the watchdog relaunches the overlay within about a second if it dies.
# Both are started in the background BEFORE the exec at the bottom of this
# file; the container is destroyed with the session, so nothing outlives it.
# Skipped, loudly, when the files are missing: a session without a watermark
# must never be silent. PICOM_CONF / WATERMARK_WATCHDOG are test-only seams.
PICOM_CONF="${PICOM_CONF:-/opt/watermark/picom.conf}"
WATERMARK_WATCHDOG="${WATERMARK_WATCHDOG:-/opt/watermark/watchdog.sh}"
if command -v picom >/dev/null 2>&1 && [ -f "$PICOM_CONF" ]; then
    (
        while true; do
            picom --config "$PICOM_CONF" >>/tmp/picom.log 2>&1
            sleep 5
        done
    ) >/dev/null 2>&1 3>&- &   # 3>&-: do not hold the BATS harness's fd 3 open (as custom_startup.sh does)
else
    echo "launch-session: WARNING picom not available -- the watermark falls back to its shaped (flickering) mode" >&2
fi
if [ -x "$WATERMARK_WATCHDOG" ]; then
    STUDENT_ID="$STUDENT_ID" SESSION_ID="$SESSION_ID" "$WATERMARK_WATCHDOG" &
else
    echo "launch-session: ERROR watermark watchdog missing at ${WATERMARK_WATCHDOG} -- this session has NO forensic watermark" >&2
fi

export WEBKIT_FORCE_SANDBOX=0
BROWSER_BIN="${BROWSER_BIN:-epiphany}"
GRADING_PANEL_URL="${VIEWER_URL}grading-panel.html?student_id=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$STUDENT_ID")&session_id=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$SESSION_ID")#token=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$GRADING_TOKEN")"
"$BROWSER_BIN" "$GRADING_PANEL_URL" &

WEASIS_BIN="${WEASIS_BIN:-/opt/weasis/bin/Weasis}"
if [ -n "$WEASIS_URI" ]; then
    exec "$WEASIS_BIN" "$WEASIS_URI"
else
    exec "$WEASIS_BIN"
fi
