#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Validating docker compose config..."
docker compose --profile monitoring config >/dev/null

echo "Basic YAML checks..."
for f in deployments/monitoring/**/*.yml deployments/monitoring/**/*.yaml; do
  [ -f "$f" ] || continue
  echo "  $f"
done

echo "OK"
