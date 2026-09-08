#!/usr/bin/env bash
# Sourced by Kasm's own entrypoint on session start (Kasm base-image convention).
# Launches Chrome pinned to the watermarked viewer, in kiosk/app mode so the
# student can't navigate away, open a normal tab, or reach devtools/downloads.
#
# STUDENT_ID / SESSION_ID are injected per-session by scripts/create-session.py
# via the Kasm API's `environment` field on request_kasm — that's what makes
# the link "individual": every minted session bakes its own watermark identity.
set -euo pipefail

STUDENT_ID="${STUDENT_ID:-UNKNOWN_STUDENT}"
SESSION_ID="${SESSION_ID:-$(date +%s)}"
# Internal-network address of Orthanc as reachable from wherever Kasm's
# containers run (e.g. "http://orthanc.internal:8042/") — never a public URL.
ORTHANC_URL="${ORTHANC_URL:?set ORTHANC_URL to Orthanc's internal address}"
FULL_URL="${VIEWER_URL}?student_id=${STUDENT_ID}&session_id=${SESSION_ID}&orthanc_url=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "${ORTHANC_URL}")"

/usr/bin/google-chrome \
  --kiosk \
  --app="${FULL_URL}" \
  --no-first-run \
  --disable-translate \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --disable-features=Translate \
  --incognito \
  &
