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
# Minted once, per-session, by scripts/create-session.py's call to
# grading-api's POST /session -- the actual credential the curl call below
# authenticates with. STUDENT_ID/SESSION_ID above are display-only now (the
# watermark text): a bare student_id was never checked against anything
# server-side, so any request could act as any student -- this token is
# what closes that gap. Required, not defaulted: a session with no token
# can't do anything useful against grading-api.
GRADING_TOKEN="${GRADING_TOKEN:?set GRADING_TOKEN to the token minted by grading-api POST /session}"
# CR #121 nit: the token is interpolated into a curl --config line wrapped
# in double quotes; a value containing " or \ could close the quote and
# inject further curl options. grading-api only ever mints token_urlsafe
# (base64url) values, so anything else means corruption or tampering --
# fail fast and loud instead of feeding curl a crafted config.
if [[ ! "$GRADING_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "ERROR: GRADING_TOKEN contains characters outside the URL-safe alphabet ([A-Za-z0-9_-]) -- refusing to launch" >&2
  exit 1
fi

# issue #102: the previous one-liner `|| CASE_JSON='{"complete": true}'`
# conflated "no case assigned" with "grading-api unreachable": a network
# or API failure silently took the done-branch, with no retry and no log
# line -- an exam session could be lost without a trace (fail-open). Now:
#  3 attempts with linear backoff (2s, 4s -- container-start races and
#  nginx reloads are the common transient causes); on exhaustion CASE_JSON
#  stays EMPTY (never a fake complete: empty already means "don't open a
#  study" downstream, while {"complete":true} asserts a state we cannot
#  have verified), a token-free error line goes to the startup log, and
#  the student still sees the panel's own "Błąd ładowania ... Spróbuj
#  ponownie" screen. A plain Weasis session launches either way -- better
#  a study-less desktop than a blank one, but never a lying one.
# API_RETRY_BACKOFF is a test seam (integer seconds; BATS uses 0).
# issue #94: the token travels in a request HEADER, fed to curl through
# --config on stdin (a <(...) process substitution -- bash forks its own
# subshell for the builtin printf, nothing execs with the token in its
# argv), so it is invisible both to `ps` and to every nginx log: query
# strings were reproduced in access.log AND error.log, argv in ps.
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
  # Deliberately NO token, no response body -- this line can land in Kasm
  # session logs; the URL is the internal service address, not a secret.
  echo "custom_startup: ERROR grading-api unreachable at ${VIEWER_URL} after ${API_ATTEMPTS} attempts -- opening a study-less session; the case was NOT completed, use the panel's retry" >&2
fi

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

# Issue #6: launch the forensic watermark overlay's watchdog in the
# background *before* the exec below. A backgrounded child survives its
# parent's later exec (exec replaces this shell's own process image, it
# doesn't touch already-started children) -- and since Kasm destroys the
# whole container on logout regardless (ephemeral, zero persistence, see
# CLAUDE.md), there's no risk of this outliving the session even though
# Weasis, not this, is the process Kasm's own service monitor tracks.
STUDENT_ID="$STUDENT_ID" SESSION_ID="$SESSION_ID" /opt/watermark/watchdog.sh &

# The grading panel (issue #5's page, never actually displayed in this
# flow until now -- a real gap the first click-through test surfaced):
# same backgrounding reasoning as the watermark above. Not wrapped in its
# own watchdog -- unlike the watermark, this isn't a forensic control that
# must survive tampering, so a simple one-shot launch is enough for now.
GRADING_PANEL_WIDTH="${GRADING_PANEL_WIDTH:-420}"
VIEWER_URL="$VIEWER_URL" GRADING_TOKEN="$GRADING_TOKEN" STUDENT_ID="$STUDENT_ID" SESSION_ID="$SESSION_ID" \
    GRADING_PANEL_WIDTH="$GRADING_PANEL_WIDTH" \
    python3 /opt/grading-panel/grading_panel_window.py &

# Weasis's own window otherwise opens wide enough to sit on top of the
# grading panel above, forcing a manual resize every session before the
# panel is even visible -- confirmed via a real click-through test. See
# arrange_windows.sh's own header comment for why this is a one-shot
# script, not a persistent watchdog.
# Same environment as the panel above: arrange_windows.sh relaunches the panel
# if the student closes it, and Weasis (same binary + study URI as the exec at
# the bottom of this file) if it exits (issue #151).
WEASIS_BIN="${WEASIS_BIN:-/opt/weasis/bin/Weasis}"
VIEWER_URL="$VIEWER_URL" GRADING_TOKEN="$GRADING_TOKEN" STUDENT_ID="$STUDENT_ID" SESSION_ID="$SESSION_ID" \
    ARRANGE_WEASIS_BIN="$WEASIS_BIN" ARRANGE_WEASIS_URI="$WEASIS_URI" \
    GRADING_PANEL_WIDTH="$GRADING_PANEL_WIDTH" /opt/grading-panel/arrange_windows.sh &

# exec (not background + exit): same reasoning as
# docker/kasm-workspace/custom_startup.sh -- the base image's service
# monitor tracks this script's own PID as the "custom_startup" service and
# expects it to keep running for as long as the app should.
#
# WEASIS_BIN is a test-only seam (issue #16's BATS suite overrides it with
# a stub that captures argv instead of launching real Weasis) -- unset in
# production, so it always resolves to the real path below.
WEASIS_BIN="${WEASIS_BIN:-/opt/weasis/bin/Weasis}"
if [ -n "$WEASIS_URI" ]; then
    exec "$WEASIS_BIN" "$WEASIS_URI"
else
    # No case assigned right now (finished all stages, or grading-api was
    # unreachable) -- still launch a plain Weasis session rather than
    # leaving the desktop blank. The grading panel (issue #5) is what
    # actually shows the "you're done" state; this is just a safe fallback.
    exec "$WEASIS_BIN"
fi
