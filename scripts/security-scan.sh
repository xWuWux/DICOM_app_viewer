#!/usr/bin/env bash
# SAST stage of the CI pipeline: bandit (Python security linter, scoped to
# grading-api's own app code -- not tests/, whose legitimate `assert`s trip
# bandit's B101 with zero security value), shellcheck (real static analysis
# for the bash scripts, beyond scripts/lint.sh's bash -n syntax-only check),
# hadolint (Dockerfile best practices -- see .hadolint.yaml for the
# threshold/rationale), and trivy (known CVEs in pinned dependencies and
# built images).
#
# Every tool here was dry-run against this repo before being wired in, and
# every real finding it surfaced was fixed first (see git log) -- this
# stage should stay green by default, not be noisy from day one.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

status=0

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "$1 not found -- $2" >&2
    exit 1
  }
}

require bandit "install it first: pip install -r docker/grading-api/requirements-dev.txt (in a venv)"
require shellcheck "install it first: https://github.com/koalaman/shellcheck#installing (a static binary, no root needed)"
require hadolint "install it first: https://github.com/hadolint/hadolint#install (a static binary, no root needed)"
require trivy "install it first: https://trivy.dev/latest/getting-started/installation/"

echo "--- bandit (docker/grading-api/app only -- not tests/, see header comment) ---"
bandit -r docker/grading-api/app || status=1

echo "--- shellcheck ---"
while IFS= read -r -d '' f; do
  shellcheck -S warning "$f" || status=1
done < <(find . -name "*.sh" -not -path "./.git/*" -not -path "*/.venv-test/*" -print0)

echo "--- hadolint ---"
for f in docker/grading-api/Dockerfile docker/viewer/Dockerfile \
  docker/kasm-workspace/Dockerfile docker/kasm-workspace-weasis/Dockerfile \
  docker/guacamole-weasis/Dockerfile; do
  hadolint "$f" || status=1
done

echo "--- trivy: dependency CVEs (fixed, CRITICAL/HIGH only) ---"
trivy fs --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed --scanners vuln \
  docker/grading-api/ || status=1

echo "--- trivy: built-image CVEs (fixed, CRITICAL/HIGH only) ---"
export ORTHANC_PASSWORD="security-scan-placeholder-not-a-real-secret"
export GRADING_COORDINATOR_KEY="security-scan-placeholder-not-a-real-secret"
docker compose -f docker-compose.yml build grading-api viewer
trivy image --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed \
  dicom_app_viewer-grading-api || status=1
trivy image --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed \
  dicom_app_viewer-viewer || status=1

if [ "$status" -eq 0 ]; then
  echo "All security scans passed."
else
  echo "One or more security scans FAILED (see above)."
fi
exit "$status"
