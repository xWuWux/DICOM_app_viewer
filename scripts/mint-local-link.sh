#!/usr/bin/env bash
# Mints a grading-api session token and prints a ready-to-open link for the
# "Quick start (local dev, no Kasm needed)" flow in README.md.
#
# Real gap this closes, found via a real tester hitting it cold: once the
# session-token system shipped (grading-api no longer trusts a bare
# student_id), opening http://localhost:8080/ with no token unconditionally
# 401s -- watermark.html's own /api/case fetch has nothing to authenticate
# with. The README's Quick Start section never got updated for that, so
# anyone following it literally just sees "Blad ladowania: HTTP 401:
# Unauthorized" in the panel, with no indication a token is even needed.
#
# Deliberately a separate script from create-session.py, not a shared code
# path: create-session.py requires Kasm's own env vars (KASM_SERVER,
# KASM_API_KEY, ...) to run at all, since minting a real Kasm session is its
# whole point -- this script exists specifically for testing without Kasm.
#
# Usage: ./scripts/mint-local-link.sh [student_id] [session_id]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${GRADING_COORDINATOR_KEY:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  set -a; source "$REPO_ROOT/.env"; set +a
fi
GRADING_COORDINATOR_KEY="${GRADING_COORDINATOR_KEY:?Set GRADING_COORDINATOR_KEY in .env -- see .env.example}"
GRADING_API_URL="${GRADING_API_URL:-http://localhost:8080}"

STUDENT_ID="${1:-STU_LOCAL_TEST}"
SESSION_ID="${2:-sess_$(date +%s)}"

RESPONSE=$(curl -sSf -X POST "${GRADING_API_URL%/}/api/session" \
  -H "Content-Type: application/json" \
  -H "X-Coordinator-Key: ${GRADING_COORDINATOR_KEY}" \
  -d "{\"student_id\": \"${STUDENT_ID}\", \"session_id\": \"${SESSION_ID}\"}")

TOKEN=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['token'])")

echo "Open this link in your browser:"
echo "${GRADING_API_URL%/}/?student_id=${STUDENT_ID}&session_id=${SESSION_ID}&token=${TOKEN}"
