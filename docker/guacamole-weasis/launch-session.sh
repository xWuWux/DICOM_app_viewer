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
# WEBKIT_FORCE_SANDBOX=0: epiphany (WebKitGTK) uses bubblewrap internally
# for its own process sandboxing, which needs unprivileged user
# namespaces -- confirmed the hard way, it crashed outright ("No
# permissions to create new namespace") without this, since this
# container doesn't have them enabled. Disabling WebKitGTK's own internal
# sandbox is the standard, minimal fix for this in a containerized
# environment (the container itself already provides isolation) rather
# than loosening the container's own namespace/seccomp restrictions more
# broadly just to satisfy one app's redundant extra sandbox layer.
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
