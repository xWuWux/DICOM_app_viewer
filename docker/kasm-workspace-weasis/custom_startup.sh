#!/usr/bin/env bash
# Sourced by Kasm's own entrypoint on session start (Kasm base-image
# convention, same as docker/kasm-workspace/custom_startup.sh).
#
# Fetches the student's current case from grading-api (via viewer's /api/
# proxy) and launches Weasis directly on the assigned study via its
# `dicom:rs` command over DICOMweb (QIDO-RS + WADO-RS), through the same
# auth-injecting proxy (:8043) the Chrome flow already uses for Orthanc --
# no separate backend authorization work needed (matches issue #4's own
# description).
#
# The exact invocation below was verified empirically against this image,
# not assumed from docs alone -- several things the docs don't make clear
# turned out to matter:
#   - dicom:rs must be sent as a weasis:// URI, not raw CLI tokens.
#     Confirmed against nroduit/Weasis's own source: ConfigData.splitArgToCmd
#     only turns "$"-prefixed main-argv tokens into commands, and even a
#     correctly "$"-prefixed raw-token invocation raced the OSGi bundle
#     startup and silently no-op'd when tested directly.
#   - the query needs an explicit "requestType=STUDY" ahead of "studyUID=".
#     Without it, RsQueryParams' request-type classification falls through
#     silently -- confirmed against org.weasis.dicom.web.InvokeImageDisplay,
#     the IHE "Invoke Image Display" profile this command implements.
#   - each command token is percent-encoded *individually*, then joined
#     with a literal "+" (not "%20" for the whole string). Matches the
#     real, working "open in Weasis" link Orthanc's own Explorer 2 plugin
#     generates (orthanc-explorer-2's orthancApi.js, getWeasisViewerUrl())
#     -- that turned out to be the actual working reference, not the
#     general Weasis docs' own hand-built examples.
#   - Orthanc's DICOMweb plugin embeds a "RetrieveURL" in every QIDO-RS
#     response, built from whatever Host header it receives. This proxy
#     (docker/viewer/default.conf.template, :8043) forwards $http_host, not
#     nginx's port-stripping $host, specifically so those retrieve URLs
#     come back with the right port -- without that fix, QIDO-RS queries
#     succeeded but every actual image download connection-refused.
set -euo pipefail

STUDENT_ID="${STUDENT_ID:-UNKNOWN_STUDENT}"
SESSION_ID="${SESSION_ID:-$(date +%s)}"
# Same-origin viewer address the Chrome image's custom_startup.sh uses for
# the watermark wrapper -- grading-api is internal-only, reached through
# viewer's /api/ proxy (see docker/viewer/default.conf.template), never
# directly.
VIEWER_URL="${VIEWER_URL:?set VIEWER_URL to the internal viewer address}"
# Port 8043, NOT Orthanc's own 8042 -- the auth-injecting proxy. Weasis is
# never taught Orthanc's real credentials, same as the Chrome flow.
ORTHANC_URL="${ORTHANC_URL:?set ORTHANC_URL to the internal auth-proxy address}"

# Best-effort: an unreachable grading-api shouldn't crash the whole
# session start (matches create-session.py's own non-fatal-readiness-check
# philosophy) -- falls through to "no case" below, which still launches a
# usable (just study-less) Weasis session instead of nothing at all.
CASE_JSON=$(curl -fsS "${VIEWER_URL}api/case?student_id=${STUDENT_ID}") || CASE_JSON='{"complete": true}'

# One python3 script (not a shell one-liner) since this needs real JSON
# parsing plus per-token percent-encoding -- same reasoning as the Chrome
# image's custom_startup.sh using python3 for its own URL encoding. Prints
# a ready-to-launch weasis:// URI on success, or nothing if there's no
# study to open right now (finished all stages, or a bad/empty response).
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

# exec (not background + exit): same reasoning as
# docker/kasm-workspace/custom_startup.sh -- the base image's service
# monitor tracks this script's own PID as the "custom_startup" service and
# expects it to keep running for as long as the app should.
if [ -n "$WEASIS_URI" ]; then
    exec /opt/weasis/bin/Weasis "$WEASIS_URI"
else
    # No case assigned right now (finished all stages, or grading-api was
    # unreachable) -- still launch a plain Weasis session rather than
    # leaving the desktop blank. The grading panel (issue #5) is what
    # actually shows the "you're done" state; this is just a safe fallback.
    exec /opt/weasis/bin/Weasis
fi
