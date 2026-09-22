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
# Baked in via ENV at image build time (see this image's Dockerfile), but
# guarded the same explicit way as ORTHANC_URL below regardless -- found
# while adding this script's BATS tests (issue #16) that it previously had
# no guard at all, so a genuinely missing value failed with bash's own
# generic "VIEWER_URL: unbound variable" under `set -u` instead of a
# message actually pointing at the problem.
VIEWER_URL="${VIEWER_URL:?set VIEWER_URL to the internal viewer address}"
# Internal-network address of Orthanc as reachable from wherever Kasm's
# containers run (e.g. "http://orthanc.internal:8042/") — never a public URL.
ORTHANC_URL="${ORTHANC_URL:?set ORTHANC_URL to the internal Orthanc address}"
# Minted once, per-session, by scripts/create-session.py's call to
# grading-api's POST /session -- the actual credential watermark.html's own
# /api/ calls authenticate with from here on. STUDENT_ID/SESSION_ID above
# are display-only now (the watermark text): a bare student_id was never
# checked against anything server-side, so any request could act as any
# student -- this token is what closes that gap. Required, not defaulted:
# a session with no token can't do anything useful against grading-api.
GRADING_TOKEN="${GRADING_TOKEN:?set GRADING_TOKEN to the token minted by grading-api POST /session}"

# Split out from the FULL_URL assignment below on purpose: nesting this
# python3 one-liner's own quoting inside an already-double-quoted bash string
# parses fine in isolation but silently breaks bash's quote-matching once
# combined with the surrounding line (confirmed the hard way — see git log).
ENCODED_ORTHANC_URL=$(python3 -c 'import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "${ORTHANC_URL}")
FULL_URL="${VIEWER_URL}?student_id=${STUDENT_ID}&session_id=${SESSION_ID}&orthanc_url=${ENCODED_ORTHANC_URL}&token=${GRADING_TOKEN}"

# exec (not background + exit): the base image's service monitor tracks
# this script's own PID as the "custom_startup" service and expects it to
# keep running for as long as the app should — backgrounding Chrome and
# letting this script exit orphans it instead of tracking it properly.
#
# CHROME_BIN is a test-only seam (issue #16's BATS suite overrides it with
# a stub that captures argv instead of launching a real browser) --
# unset in production, so it always resolves to the real path below.
CHROME_BIN="${CHROME_BIN:-/usr/bin/google-chrome}"
exec "$CHROME_BIN" \
  --kiosk \
  --app="${FULL_URL}" \
  --no-first-run \
  --disable-translate \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --disable-features=Translate \
  --incognito
