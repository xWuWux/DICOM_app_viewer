#!/usr/bin/env bash
# Guacamole-flow equivalent of docker/kasm-workspace-weasis/custom_startup.sh.
# Deliberately basic-functionality only per explicit scope for this PoC:
# no watermark overlay, no Weasis export/import lockdown, no window
# auto-tiling -- just fetch the assigned case and launch Weasis on it,
# plus a plain browser tab for the grading panel. Do not point this at
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

CASE_JSON=$(curl -fsS "${VIEWER_URL}api/case?token=${GRADING_TOKEN}") || CASE_JSON='{"complete": true}'

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
export WEBKIT_FORCE_SANDBOX=0
BROWSER_BIN="${BROWSER_BIN:-epiphany}"
GRADING_PANEL_URL="${VIEWER_URL}grading-panel.html?student_id=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$STUDENT_ID")&session_id=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$SESSION_ID")&token=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$GRADING_TOKEN")"
"$BROWSER_BIN" "$GRADING_PANEL_URL" &

WEASIS_BIN="${WEASIS_BIN:-/opt/weasis/bin/Weasis}"
if [ -n "$WEASIS_URI" ]; then
    exec "$WEASIS_BIN" "$WEASIS_URI"
else
    exec "$WEASIS_BIN"
fi
