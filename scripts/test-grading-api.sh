#!/usr/bin/env bash
# Unit tests for grading-api's 3-stage state machine (issue #8). Fast, no
# Docker/real infrastructure needed -- unlike scripts/smoke-test.sh, this
# never brings up the actual stack: a temp SQLite file per test and
# FastAPI's own TestClient drive the app in-process (see
# docker/grading-api/conftest.py and tests/test_state_machine.py).
#
# Run locally before pushing; also runs in CI (.github/workflows/ci.yml).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../docker/grading-api"

# Reused across local runs (faster repeat runs), always created fresh in
# CI (an ephemeral runner has no prior .venv-test to reuse anyway).
if [ ! -d .venv-test ]; then
  python3 -m venv .venv-test
fi
# shellcheck disable=SC1091
source .venv-test/bin/activate
pip install --quiet -r requirements-dev.txt
# Unit tests + coverage gate (issue #101): coverage was previously never
# measured in CI at all -- pytest-cov wasn't even in requirements-dev.txt
# and this script ran bare `pytest -q`, so "99% coverage" was a locally
# measured claim with no gate behind it. --cov-fail-under=95 fails the
# job below the floor (currently 100%); --cov-report=xml emits
# coverage.xml for the CI artifact (see .github/workflows/ci.yml).
pytest -q --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=95 "$@"
