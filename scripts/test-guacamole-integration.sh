#!/usr/bin/env bash
# Self-contained wrapper around scripts/test-guacamole-e2e.sh for the
# Integration Tests stage of CI: that script assumes the full stack is
# already up (its own header comment says so -- it's meant to be run by a
# developer who already ran scripts/guacamole-iac.sh). This script does
# that provisioning itself, matching scripts/smoke-test.sh's own
# self-contained/self-tearing-down pattern, so this stage needs nothing
# more than a checkout + Docker to run.
#
# Loads the committed public CT/MR fixtures, never downloads a new corpus.
# The browser journey begins at the learning-stage CT_small case.
set -euo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

status=0
# Fixed container names in Compose make project names alone insufficient.
# Never adopt a developer stack or execute a developer .env in this harness.
[ ! -e .env ] || { echo "Refusing existing .env; use a disposable checkout" >&2; exit 1; }
containers=$(docker ps -a --format '{{.Names}}') || exit 1
if printf '%s\n' "$containers" | grep -qE '^(ipcmc-|guac-weasis-)'; then
  echo "Refusing an existing application stack" >&2; exit 1
fi
volumes=$(docker volume ls -q --filter label=com.docker.compose.project=ipcmc-guac-ci) || exit 1
[ -z "$volumes" ] || { echo "Refusing existing test volumes" >&2; exit 1; }
export COMPOSE_PROJECT_NAME=ipcmc-guac-ci
export DOCKER_NETWORK=ipcmc-guac-ci_ipcmc-internal
export GUACAMOLE_URL=http://127.0.0.1:8090/guacamole/
export GRADING_API_URL=http://127.0.0.1:8080/
export ORTHANC_URL=http://127.0.0.1:8042
export GUACAMOLE_ADMIN_USERNAME=guacadmin
export GUACAMOLE_LINK_DIR="$PWD/local/ci-session-links"
unset ORTHANC_PASS ORTHANC_USER
ORTHANC_PASSWORD="$(openssl rand -hex 16)"
GRADING_COORDINATOR_KEY="$(openssl rand -hex 32)"
GUACAMOLE_DB_PASSWORD="$(openssl rand -hex 16)"
GUACAMOLE_ADMIN_PASSWORD="$(openssl rand -hex 32)"
export ORTHANC_PASSWORD GRADING_COORDINATOR_KEY GUACAMOLE_DB_PASSWORD GUACAMOLE_ADMIN_PASSWORD
unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES
cleanup() {
  result=$?
  trap - EXIT
  docker compose --env-file /dev/null -f docker-compose.remote-host.yml -f docker-compose.guacamole.yml down -v >/dev/null 2>&1 || result=1
  exit "$result"
}
trap cleanup EXIT
python3 scripts/check-compose-ports.py

echo "--- building docker/guacamole-weasis:poc ---"
docker build --build-context kasm=docker/kasm-workspace-weasis -t ipcmc/guacamole-weasis:poc docker/guacamole-weasis || { echo "build failed"; exit 1; }

echo "--- docker compose up (core + guacamole infra) ---"
docker compose --env-file /dev/null -f docker-compose.remote-host.yml -f docker-compose.guacamole.yml up -d --build \
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

echo "--- administrator authentication and existing-volume migration ---"
python3 scripts/test-guacamole-admin.py

echo "--- loading the CT_small sample instance ---"
./scripts/load-sample-studies.sh || { echo "sample data load failed"; status=1; }

if [ "$status" -eq 0 ]; then
  echo "--- running the Guacamole E2E suite ---"
  ./scripts/test-guacamole-e2e.sh || status=1
fi

exit "$status"
