#!/usr/bin/env bash
# Degraded-mode E2E tests (issue #115): for every critical dependency,
# verify the behaviour documented in docs/DEGRADED_MODES.md actually holds.
# Compose-based, same bootstrap/teardown conventions as smoke-test.sh --
# runnable identically locally and in CI (.github/workflows ci.yml, job
# "degraded-test").
#
# Rows covered here: grading-api down, SQLite locked past busy_timeout,
# Orthanc down (through the :8043 proxy), nginx rate limit hit, and (issue
# #238 point G) restart journeys: grading-api mid-exam, Orthanc, whole stack. The Kasm
# startup row's fail-open assertion (#102: API failure is never reported
# as complete) lives in docker/kasm-workspace-weasis/custom_startup.bats
# (needs no compose); Kasm/Guacamole availability and the CE cap need
# real infrastructure, same exclusion as smoke-test.sh's own header.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# --preflight runs ONLY the safety guard below and exits (0 clear / 2 refuse)
# -- pinned by scripts/test-degraded-modes.bats with a stubbed docker binary.
PREFLIGHT_ONLY=0
if [ "${1:-}" = "--preflight" ]; then PREFLIGHT_ONLY=1; fi

# SAFETY (review of #219, pattern from #214): run under a DEDICATED compose
# project so `down -v` can only ever delete THIS script's own volumes --
# never the ones of the repo's default project, where a real-data stack may
# live. DOCKER_BIN is overridable for a future bats guard test.
DOCKER_BIN="${DOCKER_BIN:-docker}"
COMPOSE_PROJECT="degraded-test"
COMPOSE=("$DOCKER_BIN" compose -p "$COMPOSE_PROJECT")

status=0
CREATED_ENV=0
CREATED_NETWORK=0

cleanup() {
  echo "--- tearing down (project $COMPOSE_PROJECT only) ---"
  "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
  [ "$CREATED_ENV" -eq 1 ] && rm -f .env
  [ "$CREATED_NETWORK" -eq 1 ] && docker network rm kasm_default_network >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Refuse to start while ANY ipcmc-* container exists (even stopped): the
# stack uses fixed container names and host ports, so racing a live stack
# would fail mid-run at best -- and the guard is what makes the -p
# isolation belt-and-braces instead of the only line of defence.
names="$("$DOCKER_BIN" ps -a --format '{{.Names}}' 2>/dev/null || true)"
if printf '%s\n' "$names" | grep -q '^ipcmc-'; then
  echo "REFUSING to start: containers named ipcmc-* already exist (live or leftover stack)." >&2
  echo "Nothing here was created or removed. Tear down or stop that stack first, or work in a" >&2
  echo "quarantined environment -- this script will not share names or ports with it." >&2
  trap - EXIT
  exit 2
fi
echo "guard: clear -- no ipcmc-* containers present"
if [ "$PREFLIGHT_ONLY" -eq 1 ]; then
  trap - EXIT
  exit 0
fi

if [ ! -f .env ]; then
  echo "ORTHANC_PASSWORD=$(openssl rand -hex 16)" > .env
  CREATED_ENV=1
fi
grep -q '^GRADING_COORDINATOR_KEY=' .env || echo "GRADING_COORDINATOR_KEY=$(openssl rand -hex 32)" >> .env
# shellcheck disable=SC1091
source .env

if ! docker network inspect kasm_default_network >/dev/null 2>&1; then
  docker network create kasm_default_network >/dev/null
  CREATED_NETWORK=1
fi

echo "--- docker compose up (project $COMPOSE_PROJECT) ---"
# --wait gates on the stack's own health checks (#69) instead of this
# script polling for a healthy endpoint. --wait-timeout separately bounds
# a wedged container to 5 min rather than letting it eat the job's whole
# budget. "${COMPOSE[@]}" keeps the -p flag on EVERY compose call below,
# teardown included.
"${COMPOSE[@]}" up -d --build --wait --wait-timeout 300 || { echo "compose up failed"; exit 1; }

# --- helpers -------------------------------------------------------------

# wait_for <want-code(s)> <max-tries> <curl args...>: poll until the
# code matches (compose up -d returns before anything is serving, same
# lesson smoke-test.sh learned) or the budget runs out; echoes the last
# code seen. <want> is either a single code or a "|502|504|" style set.
wait_for() {
  local want="$1" tries="$2"; shift 2
  local code=""
  for _ in $(seq 1 "$tries"); do
    code=$(curl -s --max-time 80 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null)
    [[ "$want" == *"|${code}|"* || "$code" == "$want" ]] && break
    sleep 2
  done
  echo "$code"
}

pass() { echo "--- $1: OK ($2) ---"; }
fail() { echo "--- $1: FAIL ($2) ---"; status=1; }

# check_eq <want> <got> <label>: if/else on purpose. The tempting
# `[ x = y ] && pass ... || fail ...` one-liner is SC2015 -- a fail() in
# the middle would run fail() no matter which side broke -- so every
# equality assertion goes through here. (Review of #219, SC2015.)
check_eq() {
  local want="$1" got="$2" label="$3"
  if [ "$want" = "$got" ]; then
    pass "$label" "HTTP $got"
  else
    fail "$label" "HTTP $got (expected HTTP $want)"
  fi
}

# Every scenario ends by bringing the stack fully back up; these two
# recovery waits gate the next scenario on real recovery, not sleep luck.
wait_api_up() { wait_for 200 30 http://localhost:8080/api/healthz; }
wait_orthanc_up() { wait_for 200 30 -u "orthanc:${ORTHANC_PASSWORD}" http://localhost:8042/system; }

echo "--- initial health ---"
# The --wait above already returned only once every health-checked
# service reported healthy; nothing else to poll here.
pass "stack came up (compose --wait, project $COMPOSE_PROJECT)" "all health checks green"

# --- 1. rate limit hit (docs/DEGRADED_MODES.md "nginx rate limit") -------
# 60 concurrent requests at the burst: must produce 429s, and -- the load
# assertion of issue #115 -- ZERO 5xx: overload is answered at the edge,
# never cascaded into the app. Also pins today's documented header gap:
# no Retry-After on 429 (add one later and this test fails until the doc
# is updated, which is the point).
echo "--- 1. rate limit: 60-request burst ---"
sleep 3  # let the token bucket settle after startup traffic
BURST_DIR=$(mktemp -d)
for i in $(seq 1 60); do
  curl -s --max-time 15 -D "$BURST_DIR/h_$i.txt" -o /dev/null -w '%{http_code}\n' \
    http://localhost:8080/api/healthz >> "$BURST_DIR/codes.txt" &
done
wait
COUNT_200=$(grep -c '^200$' "$BURST_DIR/codes.txt" || true)
COUNT_429=$(grep -c '^429$' "$BURST_DIR/codes.txt" || true)
COUNT_5XX=$(grep -cE '^5[0-9][0-9]$' "$BURST_DIR/codes.txt" || true)
if [ "$COUNT_5XX" -eq 0 ] && [ "$COUNT_429" -ge 1 ] && [ "$COUNT_200" -ge 1 ]; then
  pass "burst: 200 + 429, zero 5xx" "200=$COUNT_200 429=$COUNT_429 5xx=$COUNT_5XX"
else
  fail "burst: 200 + 429, zero 5xx" "200=$COUNT_200 429=$COUNT_429 5xx=$COUNT_5XX"
fi
# The 429 must come from nginx's own edge handler (its HTML body), never
# from a proxy loop; and no Retry-After today (documented gap).
FIRST_429_HDR=""
for f in "$BURST_DIR"/h_*.txt; do
  grep -q '^HTTP/[0-9.]* 429' "$f" && { FIRST_429_HDR="$f"; break; }
done
if [ -n "$FIRST_429_HDR" ] && ! grep -qi 'retry-after' "$FIRST_429_HDR"; then
  pass "429 sent with no Retry-After (documented gap pinned)" "headers of one captured 429"
else
  fail "429 sent with no Retry-After (documented gap pinned)" "Retry-After seen or no 429 captured"
fi
rm -rf "$BURST_DIR"
RECOVER=$(wait_api_up)
check_eq 200 "$RECOVER" "rate limit releases after burst"

# --- 2. SQLite locked past busy_timeout ----------------------------------
# A second connection holds BEGIN EXCLUSIVE for 8 s while a write request
# (POST /api/session, the cheapest authenticated write) goes through the
# app. Expectation from the doc: wait out busy_timeout=5000, then the
# standard 500 error envelope -- and afterwards the same write succeeds,
# proving nothing was half-applied.
echo "--- 2. SQLite locked past busy_timeout ---"
docker exec -d ipcmc-grading-api python3 -c \
  "import sqlite3, time; c = sqlite3.connect('${GRADING_DB_PATH:-/data/grading.db}', timeout=0); c.execute('BEGIN EXCLUSIVE'); time.sleep(8)"
sleep 1
LOCK_JSON=$(mktemp)
LOCK_CODE=$(curl -s --max-time 30 -o "$LOCK_JSON" -w '%{http_code} %{time_total}' \
  -X POST http://localhost:8080/api/session -H 'Content-Type: application/json' \
  -H "X-Coordinator-Key: ${GRADING_COORDINATOR_KEY}" \
  -d "{\"student_id\": \"DEGRADED_LOCK_$$\", \"session_id\": \"dl1\"}")
LOCK_STATUS=${LOCK_CODE%% *}
LOCK_TIME=${LOCK_CODE##* }
LOCK_WAIT_OK=$(python3 -c "print(1 if float('$LOCK_TIME') >= 4.0 else 0)")
if [ "$LOCK_STATUS" = 500 ] && [ "$LOCK_WAIT_OK" = 1 ] && grep -q '"error_code"' "$LOCK_JSON"; then
  pass "write waited for busy_timeout, then clean 500 envelope" "500 after ${LOCK_TIME}s"
else
  fail "write waited for busy_timeout, then clean 500 envelope" "code=$LOCK_STATUS time=${LOCK_TIME}s body=$(head -c 80 "$LOCK_JSON")"
fi
rm -f "$LOCK_JSON"
sleep 8  # the holder sleeps 8 s in total; this ends it
POST_LOCK=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST http://localhost:8080/api/session -H 'Content-Type: application/json' \
  -H "X-Coordinator-Key: ${GRADING_COORDINATOR_KEY}" \
  -d "{\"student_id\": \"DEGRADED_LOCK_$$\", \"session_id\": \"dl1\"}")
check_eq 200 "$POST_LOCK" "same write succeeds once the lock is gone (no state lost)"

# --- 3. grading-api down --------------------------------------------------
# The edge must say "gateway error" -- never success, never a JSON shape
# that could be mistaken for a real answer -- while the rest of the
# viewer keeps serving (the isolation claim in the doc).
echo "--- 3. grading-api down ---"
"${COMPOSE[@]}" stop grading-api >/dev/null 2>&1
sleep 2
DOWN_CODE=$(wait_for "|502|504|" 15 http://localhost:8080/api/case)
if [[ "|502|504|" == *"|$DOWN_CODE|"* ]]; then
  pass "/api/case answers a gateway error, not success" "HTTP $DOWN_CODE"
else
  fail "/api/case answers a gateway error, not success" "HTTP $DOWN_CODE (expected 502/504)"
fi
STATIC_CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/)
check_eq 200 "$STATIC_CODE" "viewer static pages unaffected"
"${COMPOSE[@]}" start grading-api >/dev/null 2>&1
RECOVER=$(wait_api_up)
check_eq 200 "$RECOVER" "grading-api recovered"

# --- 4. Orthanc down (through the auth proxy) -----------------------------
# The student-visible symptom is an empty iframe; the server-side truth
# the doc commits to is "the proxy says 502/504 quickly-ish, and grading
# keeps working independently of Orthanc".
echo "--- 4. Orthanc down ---"
"${COMPOSE[@]}" stop orthanc >/dev/null 2>&1
sleep 2
# nginx takes ~14 s (DNS gone) and up to ~60 s (stale address, connect
# timeout) -- both documented, both under the 75 s cap here. A hang
# beyond nginx's own machinery is a different bug and must fail here.
ORTHO_CODE=$(curl -s --max-time 75 -o /dev/null -w '%{http_code}' http://localhost:8043/system || echo 000)
if [[ "|502|504|" == *"|$ORTHO_CODE|"* ]]; then
  pass "auth proxy answers a gateway error for a dead Orthanc" "HTTP $ORTHO_CODE"
else
  fail "auth proxy answers a gateway error for a dead Orthanc" "HTTP $ORTHO_CODE (expected 502/504)"
fi
API_ALIVE=$(wait_for 200 15 http://localhost:8080/api/healthz)
check_eq 200 "$API_ALIVE" "grading unaffected by Orthanc outage"
"${COMPOSE[@]}" start orthanc >/dev/null 2>&1
RECOVER=$(wait_orthanc_up)
check_eq 200 "$RECOVER" "Orthanc recovered"

# --- helpers for the restart journeys (issue #238 point G) ---------------
# json_field <json> <python expression over d>: one value out of a JSON body,
# "" when the body is not JSON (a 5xx page) -- never a traceback in the log.
json_field() {
  python3 -c 'import json,sys
try:
    d = json.loads(sys.argv[1])
    print(eval(sys.argv[2], {"d": d}))
except Exception:
    print("")' "$1" "$2"
}
mint_token() {  # mint_token <student_id> -> token ("" on failure)
  json_field "$(curl -s -X POST http://localhost:8080/api/session -H 'Content-Type: application/json' \
    -H "X-Coordinator-Key: ${GRADING_COORDINATOR_KEY}" \
    -d "{\"student_id\": \"$1\", \"session_id\": \"g1\"}")" 'd["token"]'
}
get_case() {  # get_case <token> -> response body
  curl -s --max-time 15 http://localhost:8080/api/case -H "X-Grading-Token: $1"
}
code_of_case() {  # code_of_case <token> -> HTTP status of GET /api/case
  curl -s --max-time 15 -o /dev/null -w '%{http_code}' http://localhost:8080/api/case -H "X-Grading-Token: $1"
}
submit_learning() {  # submit_learning <token> <case_id> -> HTTP status
  curl -s --max-time 15 -o /dev/null -w '%{http_code}' -X POST http://localhost:8080/api/submit \
    -H 'Content-Type: application/json' \
    -d "{\"token\": \"$1\", \"case_id\": $2, \"stage\": \"learning\", \"text\": \"g\"}"
}

# --- 5. grading-api restarts mid-exam (issue #238 point G) ----------------
# The month-long course model (owner, 2026-10-14) relies on progress living
# server-side: a grading-api restart in the middle of an exam must lose
# nothing. A doctor answers the learning case, the container restarts, and
# then the SAME token still works, /api/case is still at the next case, and
# re-sending the already-answered case is refused (no duplicate, no rollback).
echo "--- 5. grading-api restart mid-exam ---"
G_STUDENT="DEGRADED_RESTART_$$"
G_TOKEN=$(mint_token "$G_STUDENT")
G_CASE1=$(json_field "$(get_case "$G_TOKEN")" 'd["case_id"]')
G_SUBMIT=$(submit_learning "$G_TOKEN" "${G_CASE1:-0}")
check_eq 200 "$G_SUBMIT" "learning answer recorded before the restart"
G_BEFORE=$(get_case "$G_TOKEN")
G_CASE2=$(json_field "$G_BEFORE" 'd["case_id"]')
G_STAGE2=$(json_field "$G_BEFORE" 'd["stage"]')
"${COMPOSE[@]}" restart grading-api >/dev/null 2>&1
RECOVER=$(wait_api_up)
check_eq 200 "$RECOVER" "grading-api back after restart"
check_eq 200 "$(code_of_case "$G_TOKEN")" "same token still valid after restart"
G_AFTER=$(get_case "$G_TOKEN")
if [ -n "$G_CASE2" ] && [ "$(json_field "$G_AFTER" 'd["case_id"]')" = "$G_CASE2" ] \
   && [ "$(json_field "$G_AFTER" 'd["stage"]')" = "$G_STAGE2" ]; then
  pass "progress survived the restart (same stage, same case)" "stage=$G_STAGE2"
else
  fail "progress survived the restart (same stage, same case)" "before=$G_STAGE2/$G_CASE2 after=$(head -c 120 <<<"$G_AFTER")"
fi
check_eq 409 "$(submit_learning "$G_TOKEN" "${G_CASE1:-0}")" "the already-answered case is refused after restart (no duplicate)"

# --- 6. Orthanc restart keeps the studies (issue #238 point G) -----------
# Studies live on the orthanc-storage volume, not in the container: a
# restart must serve the same instances, and the auth proxy must recover.
echo "--- 6. Orthanc restart keeps the studies ---"
curl -s -o /dev/null -u "orthanc:${ORTHANC_PASSWORD}" -X POST http://localhost:8042/instances \
  --data-binary @sample-data/CT_small.dcm -H "Expect:"
O_BEFORE=$(json_field "$(curl -s -u "orthanc:${ORTHANC_PASSWORD}" http://localhost:8042/statistics)" 'd["CountInstances"]')
"${COMPOSE[@]}" restart orthanc >/dev/null 2>&1
RECOVER=$(wait_orthanc_up)
check_eq 200 "$RECOVER" "Orthanc back after restart"
O_AFTER=$(json_field "$(curl -s -u "orthanc:${ORTHANC_PASSWORD}" http://localhost:8042/statistics)" 'd["CountInstances"]')
if [ -n "$O_BEFORE" ] && [ "$O_BEFORE" -ge 1 ] && [ "$O_AFTER" = "$O_BEFORE" ]; then
  pass "same instance count after restart" "$O_AFTER instance(s)"
else
  fail "same instance count after restart" "before=$O_BEFORE after=$O_AFTER"
fi
check_eq 200 "$(wait_for 200 30 http://localhost:8043/system)" "auth proxy serves Orthanc again"

# --- 7. whole stack restarts (issue #238 point G) -------------------------
# Host reboot / `docker compose restart` class: every service restarts at
# once. The doctor from scenario 5 must be exactly where they were.
echo "--- 7. whole stack restart ---"
"${COMPOSE[@]}" restart >/dev/null 2>&1
check_eq 200 "$(wait_api_up)" "grading-api back after whole-stack restart"
check_eq 200 "$(wait_orthanc_up)" "Orthanc back after whole-stack restart"
G_FINAL=$(get_case "$G_TOKEN")
if [ "$(json_field "$G_FINAL" 'd["case_id"]')" = "$G_CASE2" ] && [ "$(json_field "$G_FINAL" 'd["stage"]')" = "$G_STAGE2" ]; then
  pass "token and progress survived the whole-stack restart" "stage=$G_STAGE2"
else
  fail "token and progress survived the whole-stack restart" "after=$(head -c 120 <<<"$G_FINAL")"
fi

if [ "$status" -eq 0 ]; then
  echo "All degraded-mode checks passed (see docs/DEGRADED_MODES.md for the contract under test)."
else
  echo "One or more degraded-mode checks FAILED (see above)."
fi
exit "$status"
