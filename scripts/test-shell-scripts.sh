#!/usr/bin/env bash
# BATS tests for the shell scripts with real logic worth verifying beyond
# scripts/lint.sh's bash -n syntax check (issue #16) -- see each .bats
# file's own header comment for what it covers and why. No Docker, no real
# Kasm/Weasis/grading-api needed: the scripts under test have small,
# test-only seams (CHROME_BIN/WEASIS_BIN env vars, unset in production)
# that let these tests stub the real binary and a fake `curl` instead.
#
# Not covered here yet: docker/kasm-workspace-weasis/watchdog.sh (issue
# #16's other named target) -- that script isn't on this branch's base
# yet (it ships in issue #6's still-open PR #13); add its .bats file once
# that's merged, rather than branching off an unmerged PR (see this
# project's own git history for why that's worth avoiding).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v bats >/dev/null 2>&1; then
  echo "bats not found -- install it first (e.g. 'sudo apt-get install bats' on Debian/Ubuntu; see https://bats-core.readthedocs.io/en/stable/installation.html)" >&2
  exit 1
fi

bats docker/kasm-workspace/custom_startup.bats docker/kasm-workspace-weasis/custom_startup.bats "$@"
