#!/usr/bin/env bash
# SAST stage of the CI pipeline: bandit (Python security linter, scoped to
# grading-api's own app code -- not tests/, whose legitimate `assert`s trip
# bandit's B101 with zero security value), shellcheck (real static analysis
# for the bash scripts, beyond scripts/lint.sh's bash -n syntax-only check),
# hadolint (Dockerfile best practices -- see .hadolint.yaml for the
# threshold/rationale), trivy (known CVEs in pinned dependencies and ALL FIVE
# built images -- the three streamed-session images added by issue #238 point
# D), and gitleaks (secret scan of the FULL committed history -- in CI that
# includes every commit a PR contributes, so a leak in the diff fails too;
# exceptions are per-finding with reasons in .gitleaksignore, the built-in rule
# set stays ON via [extend] useDefault (a custom config would replace it).
# NB gitleaks git scans ALL visible refs: with fetch-depth: 0 CI sees every
# branch, so a leak pushed to ANY branch fails EVERY PR's security stage until
# that branch is deleted or fingerprinted. On purpose -- nothing hides on a
# side branch -- but do not be surprised by a red run from someone else's WIP.)
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
require gitleaks "install it first: https://github.com/gitleaks/gitleaks#install (a static binary, no root needed)"

echo "--- gitleaks: full committed history (issue #238 point D) ---"
# The clone must be full: CI checks this job out with fetch-depth: 0, and it
# contains the PR's own commits, so diff coverage comes for free. NO
# working-tree scan on purpose (#238 review): locally the tree holds gitignored
# REAL secrets (.env, local/, .venv-test/) and gitleaks dir does not honour
# .gitignore -- every developer with a working stack would fail the stage and
# print their secret paths; in CI the tree equals HEAD, which the history scan
# already covers. What it may skip is a named fingerprint per finding in
# .gitleaksignore. (Auto-loads .gitleaks.toml -- built-ins kept via
# [extend] useDefault -- from the repo root.)
gitleaks git --redact || status=1

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

# issue #238 point D: the images that actually stream pixels to the doctors were
# never scanned -- build-test builds them (so they build) but the CVE gate only
# covered grading-api + viewer. Same gate, same severity floor. Dry-run first,
# per this script's own rule: kasmweb/chrome:1.16.0 (the kasm-workspace base;
# that image adds only a chmod over it), ipcmc/dicom-viewer-weasis:mvp and
# ipcmc/guacamole-weasis:build-test all came out CLEAN at CRITICAL/HIGH
# --ignore-unfixed on 2026-10-15. If a future base bump trips this gate, decide
# fix vs a reasoned .trivyignore entry per CVE -- do not mute the gate wholesale.
echo "--- trivy: streamed-session images (issue #238 point D) ---"
docker build -q -t ipcmc/dicom-viewer:security-scan docker/kasm-workspace
docker build -q -t ipcmc/dicom-viewer-weasis:security-scan docker/kasm-workspace-weasis
docker build -q --build-context kasm=docker/kasm-workspace-weasis \
  -t ipcmc/guacamole-weasis:security-scan docker/guacamole-weasis
trivy image --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed \
  ipcmc/dicom-viewer:security-scan || status=1
trivy image --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed \
  ipcmc/dicom-viewer-weasis:security-scan || status=1
trivy image --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed \
  ipcmc/guacamole-weasis:security-scan || status=1

if [ "$status" -eq 0 ]; then
  echo "All security scans passed."
else
  echo "One or more security scans FAILED (see above)."
fi
exit "$status"
