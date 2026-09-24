#!/usr/bin/env bash
# Brings up the main docker-compose stack and checks the HTTP status codes
# that were, until now, verified by hand after every change in this project.
# Run locally, or in CI (.github/workflows/ci.yml).
#
# Deliberately NOT covered here (needs real Kasm infrastructure a CI runner
# doesn't have): actually launching a Kasm session, custom_startup.sh,
# create-session.py against a live instance, DLP settings. Those stay manual
# -- see README.md's own verification notes for how each was actually checked.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

status=0
CREATED_ENV=0
CREATED_NETWORK=0

cleanup() {
  echo "--- tearing down ---"
  docker compose down -v >/dev/null 2>&1
  [ "$CREATED_ENV" -eq 1 ] && rm -f .env
  [ "$CREATED_NETWORK" -eq 1 ] && docker network rm kasm_default_network >/dev/null 2>&1
}
trap cleanup EXIT

if [ ! -f .env ]; then
  echo "ORTHANC_PASSWORD=$(openssl rand -hex 16)" > .env
  CREATED_ENV=1
fi
# A pre-existing .env from before session tokens existed won't have this
# key yet -- append one rather than fail compose's own :? guard on it.
grep -q '^GRADING_COORDINATOR_KEY=' .env || echo "GRADING_COORDINATOR_KEY=$(openssl rand -hex 32)" >> .env
# shellcheck disable=SC1091
source .env

# docker-compose.yml expects kasm_default_network to exist (normally created
# by the Kasm Workspaces installer) -- stub it out so this file is
# smoke-testable without a real Kasm install.
if ! docker network inspect kasm_default_network >/dev/null 2>&1; then
  docker network create kasm_default_network >/dev/null
  CREATED_NETWORK=1
fi

echo "--- docker compose up ---"
docker compose up -d --build || { echo "compose up failed"; exit 1; }

# `docker compose up -d` returns as soon as containers START, not when the
# app inside is actually accepting connections -- every one of these checks
# needs to tolerate that, not just the first one. Found the hard way: an
# earlier version of this script only retried the orthanc check, and the
# other two flaked with connection-refused on a freshly-started container.
check() {
  local label="$1" want="$2" url="$3" auth="${4:-}"
  local code=""
  for _ in $(seq 1 30); do
    if [ -n "$auth" ]; then
      code=$(curl -s -o /dev/null -w "%{http_code}" -u "$auth" "$url" 2>/dev/null)
    else
      code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
    fi
    [ "$code" = "$want" ] && break
    sleep 2
  done
  if [ "$code" = "$want" ]; then
    echo "--- $label: OK (HTTP $code) ---"
  else
    echo "--- $label: FAIL, expected HTTP $want, got ${code:-<none>} ---"
    status=1
  fi
}

check "orthanc" "200" "http://localhost:8042/system" "orthanc:${ORTHANC_PASSWORD}"
check "watermarked viewer wrapper" "200" "http://localhost:8080/?student_id=CI_TEST&session_id=CI_TEST"
check "auth-injecting Orthanc proxy (401 would mean auth injection is broken)" "307" "http://localhost:8043/"
check "grading-api, direct" "200" "http://localhost:8080/api/healthz"

# grading-api's /case, /submit, /reset, /results all take a server-minted
# token now, never a bare student_id (a real gap this closed -- see
# README.md's "Lung-RADS grading" section and main.py's own module
# docstring). POST /session is the only way to get one, and it requires
# the coordinator key -- exactly the scripts/create-session.py flow.
echo "--- minting a real session token via POST /api/session ---"
SESSION_RESP=$(curl -s -X POST "http://localhost:8080/api/session" \
  -H "Content-Type: application/json" -H "X-Coordinator-Key: ${GRADING_COORDINATOR_KEY}" \
  -d "{\"student_id\": \"CI_TEST_$$\", \"session_id\": \"CI_SESSION_$$\"}")
TOKEN=$(echo "$SESSION_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)
if [ -n "$TOKEN" ]; then
  echo "--- POST /api/session: OK (minted a token) ---"
else
  echo "--- POST /api/session: FAIL, response was: $SESSION_RESP ---"
  status=1
fi

check "grading-api, first case using the minted token" "200" "http://localhost:8080/api/case?token=${TOKEN}"

# Regression checks for the token fix itself: an invalid token, or a
# missing coordinator key, must never be treated as a valid credential.
BAD_TOKEN_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8080/api/case?token=not-a-real-token")
if [ "$BAD_TOKEN_CODE" = "401" ]; then
  echo "--- grading-api rejects an invalid token: OK (401) ---"
else
  echo "--- grading-api rejects an invalid token: FAIL, got HTTP $BAD_TOKEN_CODE (expected 401) ---"
  status=1
fi

NO_KEY_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/api/session" \
  -H "Content-Type: application/json" -d '{"student_id": "attacker", "session_id": "x"}')
if [ "$NO_KEY_CODE" = "401" ] || [ "$NO_KEY_CODE" = "422" ]; then
  echo "--- POST /api/session without a coordinator key is rejected: OK (HTTP $NO_KEY_CODE) ---"
else
  echo "--- POST /api/session without a coordinator key is rejected: FAIL, got HTTP $NO_KEY_CODE ---"
  status=1
fi

# Regression check for a real bug (issue #4): the auth-injecting proxy used
# to forward nginx's $host to Orthanc, which strips the port even when the
# original request had one. Orthanc's DICOMweb plugin embeds whatever Host
# it receives into every QIDO-RS response's RetrieveURL (tag 00081190) --
# so a status-code-only check here would never catch this. QIDO *queries*
# kept returning 200 the whole time; only the RetrieveURL a real client
# (Weasis) would try to fetch images from was silently wrong. Uses the
# small committed CT_small.dcm fixture, not the larger fetch-public-samples.sh
# download, so this runs offline/in CI with no extra setup.
echo "--- loading a sample instance for the DICOMweb RetrieveURL check ---"
curl -sSf -u "orthanc:${ORTHANC_PASSWORD}" -X POST "http://localhost:8042/instances" \
  --data-binary "@sample-data/CT_small.dcm" -H "Expect:" >/dev/null || {
  echo "--- could not upload the sample instance -- skipping RetrieveURL check ---"
  status=1
}

RETRIEVE_URL=""
for _ in $(seq 1 30); do
  RETRIEVE_URL=$(curl -s "http://localhost:8043/dicom-web/studies" 2>/dev/null | python3 -c '
import sys, json
try:
    studies = json.load(sys.stdin)
    study_uid = studies[0]["0020000D"]["Value"][0]
except Exception:
    sys.exit(0)
import urllib.request
req = urllib.request.Request(
    f"http://localhost:8043/dicom-web/studies/{study_uid}/series?includefield=00081190"
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        series = json.load(resp)
    print(series[0]["00081190"]["Value"][0])
except Exception:
    pass
' 2>/dev/null)
  [ -n "$RETRIEVE_URL" ] && break
  sleep 2
done

if [[ "$RETRIEVE_URL" == *":8043"* ]]; then
  echo "--- DICOMweb RetrieveURL includes the proxy port: OK ($RETRIEVE_URL) ---"
else
  echo "--- DICOMweb RetrieveURL includes the proxy port: FAIL, got '${RETRIEVE_URL:-<none>}' (expected it to contain :8043 -- see docker/viewer/default.conf.template's \$http_host comment) ---"
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "All smoke checks passed."
else
  echo "One or more smoke checks FAILED (see above)."
fi
exit "$status"
