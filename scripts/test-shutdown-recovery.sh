#!/usr/bin/env bash
# Isolated loopback subprocess tests. No Docker and no deployment data.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
venv="${SHUTDOWN_TEST_VENV:-docker/grading-api/.venv-test}"
if [ ! -d "$venv" ]; then
  python3 -m venv "$venv"
fi
"$venv/bin/pip" install --quiet --require-hashes -r docker/grading-api/requirements.lock
"$venv/bin/pip" install --quiet -r docker/grading-api/requirements-dev.txt
test_dir=$(mktemp -d "$venv/shutdown-recovery.XXXXXX")
trap 'rm -rf "$test_dir"' EXIT
"$venv/bin/python" -m pytest -q scripts/shutdown_recovery --basetemp "$test_dir" "$@"
