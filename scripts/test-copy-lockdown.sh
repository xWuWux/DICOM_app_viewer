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
# Two parts, in order (fail fast on the cheap one first):
#   1. scripts/tests/test_copy_lockdown.py -- pure Python, no Docker,
#      runs in well under a second.
#   2. A real `docker build` of docker/kasm-workspace-weasis, then
#      inspecting the ACTUAL shipped /opt/weasis/lib/app/conf/base.json --
#      closes the gap between "the patcher script is correct in isolation"
#      and "the patcher's changes actually end up in the built image"
#      (e.g. a later Dockerfile edit that reorders/drops the RUN step
#      would pass part 1 and still ship an unlocked Weasis).
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

echo "--- inspecting the actual shipped base.json ---"
docker run --rm --entrypoint cat ipcmc/dicom-viewer-weasis:copy-lockdown-test \
  /opt/weasis/lib/app/conf/base.json \
  | python3 -c '
import json
import sys

EXPECTED_DISABLED_KEYS = {
    "weasis.show.disclaimer",
    "weasis.export.dicom",
    "weasis.export.dicom.send",
    "weasis.import.dicom",
    "weasis.import.images",
    "weasis.import.dicom.qr",
}
EXPECTED_BLANKED_BUNDLE_KEY = "felix.auto.start.110"

config = json.load(sys.stdin)
by_code = {pref["code"]: pref["value"] for pref in config["weasisPreferences"]}

failed = False
for key in EXPECTED_DISABLED_KEYS:
    if by_code.get(key) != "false":
        print(f"FAIL: shipped base.json has {key} = {by_code.get(key)!r}, expected \"false\"", file=sys.stderr)
        failed = True

if by_code.get(EXPECTED_BLANKED_BUNDLE_KEY) != "":
    print(
        f"FAIL: shipped base.json has {EXPECTED_BLANKED_BUNDLE_KEY} = "
        f"{by_code.get(EXPECTED_BLANKED_BUNDLE_KEY)!r}, expected an empty string "
        "(DICOM send / Q-R / ISO-writer bundles would still load at runtime)",
        file=sys.stderr,
    )
    failed = True

if failed:
    sys.exit(1)
print("shipped base.json matches every expected lockdown value.")
'

docker rmi ipcmc/dicom-viewer-weasis:copy-lockdown-test >/dev/null

echo "All copy-lockdown checks passed."
