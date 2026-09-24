#!/usr/bin/env bash
# Infrastructure-as-code for the Guacamole PoC flow: brings up the whole
# stack (core services + Guacamole infra) from scratch, on a fresh host,
# with one command. Deliberately a plain bash script for now, not
# Ansible -- the explicit, agreed starting point before considering a
# migration once this PoC's shape has settled (Ansible now would mean
# re-tooling twice for a flow that's still actively changing).
#
# What this does NOT do (explicitly out of scope for this basic-
# functionality PoC, see docker-compose.guacamole.yml's own header
# comment): mint per-student links (that's
# scripts/provision-guacamole-session.py, run separately per student),
# any anti-cheat/hardening, or automatic idle-session teardown (that's
# scripts/teardown-guacamole-session.py, run manually/on a schedule).
#
# Usage: ./scripts/guacamole-iac.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "--- 1. Checking prerequisites ---"
command -v docker >/dev/null 2>&1 || { echo "docker not found -- install Docker first" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose plugin not found" >&2; exit 1; }

if [ ! -f .env ]; then
  echo "No .env found -- copying .env.example. You still need to fill in real values." >&2
  cp .env.example .env
fi
# Appends any secrets a pre-existing .env from before this flow existed
# wouldn't have yet, rather than failing outright -- same pattern
# scripts/smoke-test.sh already uses for GRADING_COORDINATOR_KEY.
grep -q "^ORTHANC_PASSWORD=.\+" .env || sed -i "s/^ORTHANC_PASSWORD=.*/ORTHANC_PASSWORD=$(openssl rand -hex 16)/" .env
grep -q "^GRADING_COORDINATOR_KEY=.\+" .env || sed -i "s/^GRADING_COORDINATOR_KEY=.*/GRADING_COORDINATOR_KEY=$(openssl rand -hex 32)/" .env
grep -q "^GUACAMOLE_DB_PASSWORD=.\+" .env || sed -i "s/^GUACAMOLE_DB_PASSWORD=.*/GUACAMOLE_DB_PASSWORD=$(openssl rand -hex 16)/" .env

echo "--- 2. Building the Guacamole Weasis workspace image ---"
docker build -t "${GUACAMOLE_WEASIS_IMAGE:-ipcmc/guacamole-weasis:poc}" docker/guacamole-weasis

echo "--- 3. Bringing up the stack (core services + Guacamole infra) ---"
docker compose -f docker-compose.yml -f docker-compose.guacamole.yml up -d --build

echo "--- 4. Waiting for Guacamole's webapp to come up ---"
GUACAMOLE_URL="${GUACAMOLE_URL:-http://localhost:8090/guacamole/}"
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null "$GUACAMOLE_URL"; then
    echo "Guacamole is up."
    break
  fi
  sleep 2
done

echo "--- 5. Loading sample DICOM data (if not already loaded) ---"
./scripts/fetch-public-samples.sh
./scripts/load-sample-studies.sh

cat <<EOF

Done. Guacamole admin UI: ${GUACAMOLE_URL}
Default admin login: guacadmin / guacadmin -- CHANGE THIS before anything
beyond a local PoC (see README.md's own security note on this flow).

To mint a per-student link:
  GRADING_COORDINATOR_KEY=\$(grep GRADING_COORDINATOR_KEY .env | cut -d= -f2) \\
    python3 scripts/provision-guacamole-session.py --student-id STU_12345

To tear a session down once finished:
  python3 scripts/teardown-guacamole-session.py --student-id STU_12345 --session-id <from the link>
EOF
