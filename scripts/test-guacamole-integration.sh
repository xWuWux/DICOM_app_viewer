#!/usr/bin/env bash
# Self-contained wrapper around scripts/test-guacamole-e2e.sh for the
# Integration Tests stage of CI: that script assumes the full stack is
# already up (its own header comment says so -- it's meant to be run by a
# developer who already ran scripts/guacamole-iac.sh). This script does
# that provisioning itself, matching scripts/smoke-test.sh's own
# self-contained/self-tearing-down pattern, so this stage needs nothing
# more than a checkout + Docker to run.
#
# Deliberately loads only the small committed CT_small.dcm fixture (via
# scripts/load-sample-studies.sh), not the larger fetch-public-samples.sh
# download -- the E2E test only ever exercises a brand-new student, who
# always starts on the learning-stage seed case (CT_small), so BRAINIX
# isn't needed here and would only slow this stage down for no benefit
# (same reasoning scripts/smoke-test.sh already uses).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

status=0
CREATED_ENV=0
CREATED_NETWORK=0

cleanup() {
  echo "--- tearing down ---"
  docker compose -f docker-compose.yml -f docker-compose.guacamole.yml down -v >/dev/null 2>&1
  [ "$CREATED_ENV" -eq 1 ] && rm -f .env
  [ "$CREATED_NETWORK" -eq 1 ] && docker network rm kasm_default_network >/dev/null 2>&1
}
trap cleanup EXIT

if [ ! -f .env ]; then
  echo "ORTHANC_PASSWORD=$(openssl rand -hex 16)" > .env
  CREATED_ENV=1
fi
grep -q '^GRADING_COORDINATOR_KEY=' .env || echo "GRADING_COORDINATOR_KEY=$(openssl rand -hex 32)" >> .env
grep -q '^GUACAMOLE_DB_PASSWORD=' .env || echo "GUACAMOLE_DB_PASSWORD=$(openssl rand -hex 16)" >> .env
# set -a so these are actually exported to test-guacamole-e2e.sh's own
# subprocess below (which reads them from its environment to pass into
# `docker run -e`) -- a plain `source` only sets shell variables in this
# process, confirmed the hard way: GRADING_COORDINATOR_KEY wasn't visible
# to the child script without this.
set -a
# shellcheck disable=SC1091
source .env
set +a

if ! docker network inspect kasm_default_network >/dev/null 2>&1; then
  docker network create kasm_default_network >/dev/null
  CREATED_NETWORK=1
fi

echo "--- building docker/guacamole-weasis:poc ---"
docker build -t ipcmc/guacamole-weasis:poc docker/guacamole-weasis || { echo "build failed"; exit 1; }

echo "--- docker compose up (core + guacamole infra) ---"
docker compose -f docker-compose.yml -f docker-compose.guacamole.yml up -d --build \
  || { echo "compose up failed"; exit 1; }

echo "--- waiting for Guacamole's webapp ---"
GUACAMOLE_READY=0
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null "http://localhost:8090/guacamole/"; then
    GUACAMOLE_READY=1
    break
  fi
  sleep 2
done
if [ "$GUACAMOLE_READY" -ne 1 ]; then
  echo "Guacamole never became ready"
  exit 1
fi

echo "--- loading the CT_small sample instance ---"
./scripts/load-sample-studies.sh || { echo "sample data load failed"; status=1; }

if [ "$status" -eq 0 ]; then
  echo "--- running the Guacamole E2E suite ---"
  ./scripts/test-guacamole-e2e.sh || status=1
fi

exit "$status"
