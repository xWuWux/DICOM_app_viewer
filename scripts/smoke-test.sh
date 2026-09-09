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
cd "$(dirname "${BASH_SOURCE[0]}")/.."

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

if [ "$status" -eq 0 ]; then
  echo "All smoke checks passed."
else
  echo "One or more smoke checks FAILED (see above)."
fi
exit "$status"
