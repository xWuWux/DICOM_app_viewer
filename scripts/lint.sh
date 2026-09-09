#!/usr/bin/env bash
# Fast checks needing no running infrastructure: bash syntax + compose file
# validity. Run this locally before pushing; also runs in CI
# (.github/workflows/ci.yml). Complements scripts/smoke-test.sh, which
# actually brings the stack up.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

status=0

echo "--- bash syntax check ---"
while IFS= read -r -d '' f; do
  echo "  $f"
  bash -n "$f" || status=1
done < <(find . \( -name "*.sh" -o -name "*.envsh" \) -not -path "./.git/*" -print0)

echo "--- docker compose config validation ---"
# A placeholder value is fine here -- config only renders/validates the
# compose model, it doesn't start anything or need a real credential.
# Confirmed separately that `config` does NOT require kasm_default_network
# (docker-compose.yml's external network) to actually exist.
export ORTHANC_PASSWORD="lint-only-placeholder-not-a-real-secret"
docker compose -f docker-compose.yml config >/dev/null || status=1
docker compose -f docker-compose.remote-host.yml config >/dev/null || status=1

if [ "$status" -eq 0 ]; then
  echo "All lint checks passed."
else
  echo "One or more lint checks FAILED (see above)."
fi
exit "$status"
