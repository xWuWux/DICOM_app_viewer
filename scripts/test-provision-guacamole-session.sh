#!/usr/bin/env bash
# Unit tests for scripts/provision-guacamole-session.py (issue #53). Fast,
# no Docker/real infrastructure needed -- unlike
# scripts/test-guacamole-integration.sh, this never brings up the actual
# stack: subprocess.run and urllib.request.urlopen are both mocked (see
# scripts/tests/test_provision_guacamole_session.py).
#
# Run locally before pushing; also runs in CI (.github/workflows/ci.yml).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/tests" || exit 1

if [ ! -d .venv-test ]; then
  python3 -m venv .venv-test
fi
# shellcheck disable=SC1091
source .venv-test/bin/activate
pip install --quiet -r requirements-dev.txt
pytest -q "$@"
