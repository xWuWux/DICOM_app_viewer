#!/usr/bin/env bash
# "Does it compile" stage of the CI pipeline -- builds every image in this
# repo (both compose files and the 3 standalone Kasm/Guacamole workspace
# Dockerfiles), but never runs any of them. Deliberately faster/cheaper
# than scripts/smoke-test.sh (which also brings the stack up and hits real
# endpoints) so a build-breaking change fails here first, before the
# slower Security Scan / Unit & Shell / Integration stages even start.
#
# Placeholder secrets below are fine here -- build only renders/validates
# the compose model and builds images, it doesn't start anything or need a
# real credential (same reasoning as scripts/lint.sh's own compose config
# validation).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

export ORTHANC_PASSWORD="build-test-placeholder-not-a-real-secret"
export GRADING_COORDINATOR_KEY="build-test-placeholder-not-a-real-secret"
export GUACAMOLE_DB_PASSWORD="build-test-placeholder-not-a-real-secret"

echo "--- docker-compose.yml ---"
docker compose -f docker-compose.yml build

echo "--- docker-compose.remote-host.yml ---"
docker compose -f docker-compose.remote-host.yml build

echo "--- docker-compose.yml + docker-compose.guacamole.yml ---"
docker compose -f docker-compose.yml -f docker-compose.guacamole.yml build

echo "--- docker/kasm-workspace ---"
docker build -q -t ipcmc/dicom-viewer:build-test docker/kasm-workspace

echo "--- docker/kasm-workspace-weasis ---"
docker build -q -t ipcmc/dicom-viewer-weasis:build-test docker/kasm-workspace-weasis

echo "--- docker/guacamole-weasis ---"
docker build -q -t ipcmc/guacamole-weasis:build-test docker/guacamole-weasis

echo "All images built successfully."
