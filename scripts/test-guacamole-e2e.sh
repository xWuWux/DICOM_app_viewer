#!/usr/bin/env bash
# Full browser-driven E2E test for the Guacamole PoC flow
# (docker/guacamole/tests/test_guacamole_e2e.py) -- runs inside the
# official Playwright Python Docker image, which bundles Chromium's own
# system library dependencies already (libnspr4, libnss3, etc.).
# `playwright install --with-deps` needs sudo to install those on the
# host directly, which isn't guaranteed to be available/non-interactive
# everywhere this runs -- confirmed the hard way in this project's own
# dev environment.
#
# Docker CLI is bind-mounted from the host (not installed via apt inside
# the container) -- the test's own provisioning/teardown scripts need to
# manage per-student containers via the host's Docker daemon
# (docker-outside-of-docker), and mounting the binary directly avoids the
# overhead and external-network dependency of an apt-get inside every
# test run.
#
# Needs the full stack already running:
#   docker compose -f docker-compose.yml -f docker-compose.guacamole.yml up -d --build
# and docker/guacamole-weasis:poc already built:
#   docker build -t ipcmc/guacamole-weasis:poc docker/guacamole-weasis
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PLAYWRIGHT_VERSION="1.63.0"

docker run --rm \
  --network host \
  -v "$(pwd)":/workspace \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /usr/bin/docker:/usr/bin/docker:ro \
  -w /workspace \
  -e GRADING_COORDINATOR_KEY="${GRADING_COORDINATOR_KEY:?Set GRADING_COORDINATOR_KEY -- see .env.example}" \
  -e GUACAMOLE_URL="${GUACAMOLE_URL:-http://localhost:8090/guacamole/}" \
  -e GUACAMOLE_ADMIN_USERNAME="${GUACAMOLE_ADMIN_USERNAME:-guacadmin}" \
  -e GUACAMOLE_ADMIN_PASSWORD="${GUACAMOLE_ADMIN_PASSWORD:-guacadmin}" \
  -e GRADING_API_URL="${GRADING_API_URL:-http://localhost:8080/}" \
  -e DOCKER_NETWORK="${DOCKER_NETWORK:-dicom_app_viewer_ipcmc-internal}" \
  -e GUACAMOLE_WEASIS_IMAGE="${GUACAMOLE_WEASIS_IMAGE:-ipcmc/guacamole-weasis:poc}" \
  "mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble" \
  bash -c "pip install --quiet -r docker/guacamole/tests/requirements-dev.txt && pytest -q -s docker/guacamole/tests/test_guacamole_e2e.py"
