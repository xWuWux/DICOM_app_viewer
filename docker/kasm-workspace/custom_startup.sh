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
ORTHANC_URL="${ORTHANC_URL:?set ORTHANC_URL to the internal Orthanc address}"

# Split out from the FULL_URL assignment below on purpose: nesting this
# python3 one-liner's own quoting inside an already-double-quoted bash string
# parses fine in isolation but silently breaks bash's quote-matching once
# combined with the surrounding line (confirmed the hard way — see git log).
ENCODED_ORTHANC_URL=$(python3 -c 'import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "${ORTHANC_URL}")
FULL_URL="${VIEWER_URL}?student_id=${STUDENT_ID}&session_id=${SESSION_ID}&orthanc_url=${ENCODED_ORTHANC_URL}"

# exec (not background + exit): the base image's service monitor tracks
# this script's own PID as the "custom_startup" service and expects it to
# keep running for as long as the app should — backgrounding Chrome and
# letting this script exit orphans it instead of tracking it properly.
exec /usr/bin/google-chrome \
  --kiosk \
  --app="${FULL_URL}" \
  --no-first-run \
  --disable-translate \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --disable-features=Translate \
  --incognito
