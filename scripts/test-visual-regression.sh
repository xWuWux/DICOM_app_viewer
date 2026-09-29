#!/usr/bin/env bash
# Self-contained visual regression test runner: brings up the main stack,
# loads the small committed sample fixtures, runs the Playwright-based
# visual snapshot suite (docker/viewer/tests/test_visual_regression.py)
# inside the official Playwright docker image, and tears everything down
# on exit -- same self-contained/self-tearing-down pattern as
# scripts/smoke-test.sh and scripts/test-guacamole-integration.sh.
#
# Usage:
#   ./scripts/test-visual-regression.sh              # compare against committed baselines
#   ./scripts/test-visual-regression.sh --update-snapshots
#                                                     # (re)generate baselines -- review the
#                                                     # resulting images under
#                                                     # docker/viewer/tests/__snapshots__/
#                                                     # before committing them
#
# On a mismatch, diff/actual/expected images are written under
# docker/viewer/tests/__snapshot_failures__/ (gitignored) -- see those to
# see exactly what changed. In CI, that directory is uploaded as a build
# artifact (see .github/workflows/ci.yml) since it doesn't survive the
# runner otherwise.
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
grep -q '^GRADING_COORDINATOR_KEY=' .env || echo "GRADING_COORDINATOR_KEY=$(openssl rand -hex 32)" >> .env
set -a
# shellcheck disable=SC1091
source .env
set +a

if ! docker network inspect kasm_default_network >/dev/null 2>&1; then
  docker network create kasm_default_network >/dev/null
  CREATED_NETWORK=1
fi

echo "--- docker compose up ---"
docker compose up -d --build || { echo "compose up failed"; exit 1; }

echo "--- waiting for grading-api ---"
READY=0
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null "http://localhost:8080/api/healthz"; then
    READY=1
    break
  fi
  sleep 2
done
if [ "$READY" -ne 1 ]; then
  echo "grading-api never became ready"
  exit 1
fi

echo "--- loading sample data (small committed fixtures only, no network fetch needed) ---"
./scripts/load-sample-studies.sh || { echo "sample data load failed"; status=1; }

if [ "$status" -eq 0 ]; then
  echo "--- running the visual regression suite ---"
  docker run --rm --network host \
    -v "$(pwd)":/workspace -w /workspace/docker/viewer/tests \
    -e GRADING_COORDINATOR_KEY="${GRADING_COORDINATOR_KEY}" \
    -e GRADING_API_URL="http://localhost:8080/api" \
    mcr.microsoft.com/playwright/python:v1.63.0-noble \
    bash -c "pip install --quiet -r requirements-dev.txt && pytest -q --base-url http://localhost:8080 $*" \
    || status=1
fi

exit "$status"
