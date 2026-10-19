#!/usr/bin/env bash
# Regression tests for every copying mechanism this project claims to have
# locked down: native Weasis export/import/send/Q-R (issue #7) and
# KasmVNC's rich/binary clipboard-DLP gap (README.md's DLP section) --
# text/screen/Weasis-export copy, respectively. See
# scripts/tests/test_copy_lockdown.py's own module docstring for exactly
# what this does and does not cover (the Kasm admin-console Group-level
# DLP toggles are explicitly out of scope -- no live Kasm instance exists
# in CI to re-check those against).
#
# Three parts, in order (fail fast on the cheap one first):
#   1. scripts/tests/test_copy_lockdown.py -- pure Python, no Docker,
#      runs in well under a second.
#   2. A real `docker build` of docker/kasm-workspace-weasis, then
#      inspecting the ACTUAL shipped /opt/weasis/lib/app/conf/base.json --
#      closes the gap between "the patcher script is correct in isolation"
#      and "the patcher's changes actually end up in the built image"
#      (e.g. a later Dockerfile edit that reorders/drops the RUN step
#      would pass part 1 and still ship an unlocked Weasis).
#   3. The same build+inspect for docker/guacamole-weasis (issue #263):
#      it runs the identical patcher through the "kasm" build context, so
#      both vendor images must ship the identical lockdown state. The
#      Guacamole transport-layer clipboard toggles are NOT checked here
#      (connection-time settings, verified live under #263's part 1).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

echo "--- scripts/tests/test_copy_lockdown.py ---"
(
  cd scripts/tests || exit 1
  if [ ! -d .venv-test ]; then
    python3 -m venv .venv-test
  fi
  # shellcheck disable=SC1091
  source .venv-test/bin/activate
  pip install --quiet -r requirements-dev.txt
  pytest -q test_copy_lockdown.py
)

echo "--- building docker/kasm-workspace-weasis (real image, not a fixture) ---"
docker build -q -t ipcmc/dicom-viewer-weasis:copy-lockdown-test docker/kasm-workspace-weasis

echo "--- inspecting the actual shipped base.json (Kasm image) ---"
inspect_lockdown() {
  docker run --rm --entrypoint cat "$1" /opt/weasis/lib/app/conf/base.json | python3 scripts/tests/inspect_lockdown_basejson.py
}
inspect_lockdown ipcmc/dicom-viewer-weasis:copy-lockdown-test
docker rmi ipcmc/dicom-viewer-weasis:copy-lockdown-test >/dev/null

echo "--- building docker/guacamole-weasis (issue #263: same patcher via the kasm build context) ---"
docker build -q --build-context kasm=docker/kasm-workspace-weasis -t ipcmc/guacamole-weasis:copy-lockdown-test docker/guacamole-weasis

echo "--- inspecting the actual shipped base.json (Guacamole image) ---"
inspect_lockdown ipcmc/guacamole-weasis:copy-lockdown-test
docker rmi ipcmc/guacamole-weasis:copy-lockdown-test >/dev/null

echo "All copy-lockdown checks passed (both vendor images)."
