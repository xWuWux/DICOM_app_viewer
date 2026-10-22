#!/usr/bin/env bash
# Blue-team item 0 (issue #195): measure what the Orthanc proxy on :8043 lets a
# session see, per the red-team task list (docs/RED_TEAM_TASKS.md, RT-T-01..04,
# delivered on PR #192). PUBLIC SAMPLE DATA ONLY -- throwaway stack,
# self-tearing-down. Never point this at a stack holding real data.
#
# Modes:
#   default          run every probe, PRINT the gap table, exit per result
#                    (gaps on legacy paths are the KNOWN open state until the
#                    callers switch + PR-B2 closes them)
#   --ci-scoped      gate scoped reads and all write denials; report the explicit
#                    legacy read/search exposure separately (#195), not as isolation
#   --assert-scoped  same run; non-zero exit additionally means "a session
#                    could reach data it was not issued" -- the future gate.
#
# Evidence rules (from the task list): HTTP codes, counts, sizes, SHA-256 of
# UIDs -- never payloads, never tag values, never the token or the derived
# /s/<key>/ secret.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

ASSERT_SCOPED=0
CI_SCOPED=0
PREFLIGHT_ONLY=0
SELFTEST=""
ST_GAPS=0
ST_ASSERT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --assert-scoped) ASSERT_SCOPED=1 ;;
    --ci-scoped) CI_SCOPED=1; ASSERT_SCOPED=1 ;;
    --preflight) PREFLIGHT_ONLY=1 ;;
    --selftest-mask) SELFTEST="mask" ;;
    --selftest-exit)
      SELFTEST="exit"
      ST_GAPS="${2:-0}"; ST_ASSERT="${3:-0}"
      case "$ST_GAPS$ST_ASSERT" in
        *[!0-9]*|"") echo "usage: $0 --selftest-exit <gaps> <assert-scoped> (integers)" >&2; exit 2 ;;
      esac
      shift 2 2>/dev/null || shift "$#" ;;
    *) echo "usage: $0 [--assert-scoped | --ci-scoped] [--preflight] [--selftest-mask] [--selftest-exit <gaps> <assert-scoped>]" >&2; exit 2 ;;
  esac
  shift
done

# SAFETY (p2 review of this PR): run under a DEDICATED compose project so
# down -v can only ever delete this script's own volumes -- never the ones
# of the repo's default project, where the owner's real-data stack may
# live. DOCKER_BIN is overridable for the bats guard test.
DOCKER_BIN="${DOCKER_BIN:-docker}"
COMPOSE_PROJECT="bluet-scope"
COMPOSE=("$DOCKER_BIN" compose -p "$COMPOSE_PROJECT" -f docker-compose.yml)

PROXY="http://localhost:8043"
API="http://localhost:8080/api"
CREATED_ENV=0
STARTED_STACK=0
CREATED_NETWORK=0

cleanup() {
  local result=$?
  echo "--- tearing down (project $COMPOSE_PROJECT only) ---"
  if [ "$STARTED_STACK" -eq 1 ]; then
    "${COMPOSE[@]}" down -v >/dev/null 2>&1 || { echo "ERROR: test stack cleanup failed" >&2; result=1; }
  fi
  if [ "$CREATED_NETWORK" -eq 1 ]; then
    "$DOCKER_BIN" network rm kasm_default_network >/dev/null 2>&1 || true
  fi
  if [ "$CREATED_ENV" -eq 1 ]; then
    rm -f .env
    rmdir local 2>/dev/null || true  # only a newly created empty bind directory
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Refuse to start while ANY ipcmc-* container exists (even stopped): the
# stack uses fixed container names and host ports, so racing the owner's
# live stack would fail mid-run at best -- and the guard is what makes the
# -p isolation belt-and-braces instead of the only line of defence.
preflight() {
  local names volumes
  names="$("$DOCKER_BIN" ps -a --format '{{.Names}}' 2>/dev/null)" || {
    echo "REFUSING to start: Docker inventory unavailable." >&2; exit 2;
  }
  if printf '%s
' "$names" | grep -q '^ipcmc-'; then
    echo "REFUSING to start: containers named ipcmc-* already exist (live or leftover stack)." >&2
    echo "Nothing here was created or removed. Tear down or stop that stack first, or work in a" >&2
    echo "quarantined environment -- this script will not share names or ports with it." >&2
    exit 2
  fi
  volumes="$("$DOCKER_BIN" volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT")" || {
    echo "REFUSING to start: Docker volume inventory unavailable." >&2; exit 2;
  }
  if [ -n "$volumes" ]; then
    echo "REFUSING to start: test-project volumes already exist; inspect them first." >&2
    exit 2
  fi
  echo "guard: clear -- no ipcmc-* containers present"
}
# Exit contract (item 0 of #195, agreed with p2): gaps are RECORDED and a
# default run exits 0. --assert-scoped gates every finding; --ci-scoped
# gates all except the explicitly marked legacy read/search exposure. Exit 2 is reserved for usage and
# preflight-guard errors. finalize_code <gaps> <assert_scoped> -> code.
finalize_code() {
  if [ "$1" -gt 0 ] && [ "$2" -eq 1 ]; then echo 1; else echo 0; fi
}
# Path display: never print the derived /s/<key>/ secret or Orthanc internal
# ids (p2 review: the first pass leaked 43/64 hex chars per scoped line).
display_path() {
  local shown="$1"
  shown="${shown//${KEY_A:-}/<key>}"
  shown="${shown//${KEY_B:-}/<key>}"
  shown="${shown//${UID_A:-}/<study>}"
  shown="${shown//${UID_B:-}/<study>}"
  printf '%s' "$shown" | sed -E "s#/[0-9a-f]{8}-[0-9a-f-]{20,}#/<id>#g"
}
# A rejection must be an authorization denial, never a redirect, outage,
# missing route, or malformed request. Bulk success may legitimately be 206.
response_ok() {
  case "$1:$2" in
    own-ok:200|bulk-ok:200|bulk-ok:206|blocked:401|blocked:403) return 0 ;;
    *) return 1 ;;
  esac
}
if [ "$SELFTEST" = "mask" ]; then
  trap - EXIT
  KEY_A=$(printf "a%.0s" $(seq 1 64))
  KEY_B=$(printf "b%.0s" $(seq 1 64))
  UID_A=1.2.826.0.1.3680043.2.1125.01.01
  UID_B=1.2.3.4
  display_path "/s/$KEY_A/dicom-web/studies/1.2.826.0.1.3680043.2.1125.01.01/series"
  echo
  display_path "/studies/27f7126f-0011-4222-8333-444455556666/archive"
  echo
  exit 0
fi
if [ "$SELFTEST" = "exit" ]; then
  trap - EXIT
  exit "$(finalize_code "$ST_GAPS" "$ST_ASSERT")"
fi

preflight
if [ "$PREFLIGHT_ONLY" -eq 1 ]; then
  trap - EXIT
  exit 0
fi

# Refuse developer configuration rather than modifying it or mounting a real
# case list. Run from a clean checkout; only committed public fixtures are used.
if [ -e .env ] || [ -e local ]; then
  echo "REFUSING to start: use a clean checkout without .env or local/." >&2
  exit 2
fi
umask 077
printf 'ORTHANC_PASSWORD=%s\nGRADING_COORDINATOR_KEY=%s\n' \
  "$(openssl rand -hex 16)" "$(openssl rand -hex 32)" > .env
CREATED_ENV=1
# shellcheck disable=SC1091
source .env
export ORTHANC_PASSWORD GRADING_COORDINATOR_KEY
export GRADING_CASES_FILE= GRADING_RESET_ENABLED=0
# Never inherit a developer's override stack or alternate project/file.
unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES

echo "--- bring up disposable stack; committed CT/MR fixtures only ---"
if ! "$DOCKER_BIN" network inspect kasm_default_network >/dev/null 2>&1; then
  "$DOCKER_BIN" network create kasm_default_network >/dev/null
  CREATED_NETWORK=1
fi
STARTED_STACK=1
"${COMPOSE[@]}" up -d --build --wait --wait-timeout 300 >/dev/null
# Bound every request, including bulk probes; never follow redirects to a
# potentially unscoped path. curl errors are fatal, not authorization evidence.
curl() { command curl --connect-timeout 5 --max-time 30 "$@"; }
admin() { curl -sf -u "orthanc:${ORTHANC_PASSWORD}" "http://localhost:8042$1"; }
for fixture in CT_small MR_small; do
  git ls-files --error-unmatch "sample-data/$fixture.dcm" >/dev/null
  curl -sf -u "orthanc:${ORTHANC_PASSWORD}" -X POST http://localhost:8042/instances \
    --data-binary "@sample-data/$fixture.dcm" -H 'Expect:' >/dev/null
done

mint() {
  curl -sf -X POST "$API/session" -H "Content-Type: application/json" \
    -H "X-Coordinator-Key: ${GRADING_COORDINATOR_KEY}" \
    -d "{\"student_id\": \"$1\", \"session_id\": \"1\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])'
}
issued_uid() {
  curl -sf "$API/case" -H "X-Grading-Token: $1" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("orthanc_study_uid",""))'
}
key_of() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
sh_of()  { printf '%s' "$1" | sha256sum | cut -d' ' -f1 | cut -c1-12; }

TOK_A=$(mint BLUET_A) || { echo "mint failed"; exit 1; }
TOK_B=$(mint BLUET_B) || { echo "mint failed"; exit 1; }
# Every fresh doctor starts at the SAME first learning case -- advance B one
# real submit so A keeps the learning case and B holds the assessment one
# (p2's tested fix from the #214 review).
CASE_B=$(curl -sf "$API/case" -H "X-Grading-Token: $TOK_B")
CASE_ID_B=$(printf '%s' "$CASE_B" | python3 -c 'import json,sys; print(json.load(sys.stdin)["case_id"])')
curl -sf -o /dev/null -X POST "$API/submit" -H "Content-Type: application/json" \
  -d "{\"token\": \"$TOK_B\", \"case_id\": $CASE_ID_B, \"stage\": \"learning\", \"text\": \"advance\"}" || { echo "could not advance B"; exit 1; }
UID_A=$(issued_uid "$TOK_A")
UID_B=$(issued_uid "$TOK_B")
KEY_A=$(key_of "$TOK_A")
KEY_B=$(key_of "$TOK_B")
if [ -z "$UID_A" ] || [ -z "$UID_B" ] || [ "$UID_A" = "$UID_B" ]; then
  echo "expected two distinct issued cases, got hashes '$(sh_of "$UID_A")' '$(sh_of "$UID_B")'"
  echo "(empty usually means the stage's case list is exhausted -- inspect the disposable test-project state)"
  exit 1
fi
echo "--- A holds <hash $(sh_of "$UID_A")>, B holds <hash $(sh_of "$UID_B")>; token/key never printed ---"

status=0
legacy_gaps=0
probe() { # <task> <class> <blocked|own-ok|bulk-ok|legacy-open> <path> [curl args]
  local task="$1" class="$2" expect="$3" path="$4"; shift 4
  local out c n verdict="ok"
  out=$(curl -s -o /dev/null -w "%{http_code} %{size_download}" "$@" "${PROXY}${path}")
  c="${out%% *}"; n="${out##* }"
  if [ "$expect" = legacy-open ]; then
    if [ "$c" = 200 ] || [ "$c" = 206 ]; then
      verdict="KNOWN-EXPOSURE(#195)"; legacy_gaps=$((legacy_gaps + 1))
      [ "$CI_SCOPED" -eq 1 ] || status=$((status + 1))
    elif ! response_ok blocked "$c"; then
      verdict="UNEXPECTED-RESPONSE"; status=$((status + 1))
    fi
  elif ! response_ok "$expect" "$c"; then
    verdict="FAIL"; status=$((status + 1))
  fi
  printf '%-8s %-6s %-46s code=%s bytes=%s %s\n' "$task" "$class" "$(display_path "$path")" "$c" "$n" "$verdict"
}
probe_bulk() { probe "$1" "$2" "$3" "$4" -r 0-0 "${@:5}"; }

echo; echo "=== RT-T-01 enumeration ==="
probe RT-T-01 scoped blocked "/s/$KEY_A/dicom-web/studies"
probe RT-T-01 scoped own-ok  "/s/$KEY_A/dicom-web/studies?StudyInstanceUID=$UID_A"
probe RT-T-01 legacy legacy-open "/dicom-web/studies"
probe RT-T-01 legacy legacy-open "/patients"

echo; echo "=== RT-T-02 bulk retrieval (archive via legacy REST) ==="
# Discover real existing targets through the administrator setup channel.
# A blocked public listing must never cause bulk/file/tag probes to be skipped.
IDS=$(admin /studies)
FIRST_ID=$(printf '%s' "$IDS" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')
probe RT-T-02 legacy legacy-open "/studies"
probe_bulk RT-T-02 legacy legacy-open "/studies/$FIRST_ID/archive"
probe_bulk RT-T-02 legacy legacy-open "/studies/$FIRST_ID/media"
probe_bulk RT-T-02 scoped blocked "/s/$KEY_A/dicom-web/studies/$UID_B"
probe_bulk RT-T-02 scoped bulk-ok "/s/$KEY_B/dicom-web/studies/$UID_B"

echo; echo "=== RT-T-03 raw instance file + tags ==="
SID=$(admin "/studies/$FIRST_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Series"][0])')
IID=$(admin "/series/$SID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Instances"][0])')
probe_bulk RT-T-03 legacy legacy-open "/instances/$IID/file"
probe RT-T-03 legacy legacy-open "/instances/$IID/tags"
probe RT-T-03 scoped blocked "/s/$KEY_A/dicom-web/studies/$UID_B/metadata"
probe RT-T-03 scoped own-ok  "/s/$KEY_A/dicom-web/studies/$UID_A/metadata"

echo; echo "=== RT-T-04 write/delete attempts ==="
probe RT-T-04 legacy blocked "/studies/${FIRST_ID:-none}" -X DELETE
probe RT-T-04 legacy blocked "/instances" -X POST --data-binary "@sample-data/CT_small.dcm"
probe RT-T-04 legacy legacy-open "/tools/find" -X POST -H "Content-Type: application/json" -d '{"Level":"Study","Query":{}}'

echo; echo "=== cross-session scoped probes (#196 core property) ==="
probe ITEM1 scoped blocked "/s/$KEY_A/dicom-web/studies/$UID_B/series"
probe ITEM1 scoped own-ok  "/s/$KEY_A/dicom-web/studies/$UID_A/series"

# The current policy (#236) permits B, now in assessment, to read learning
# studies too. Cross-student does not mean mutually exclusive study access.
probe ITEM1 scoped own-ok "/s/$KEY_B/dicom-web/studies/$UID_B/metadata"
probe ITEM1 scoped own-ok "/s/$KEY_B/dicom-web/studies/$UID_A/metadata"
probe RT-T-04 scoped blocked "/s/$KEY_A/dicom-web/studies/$UID_A" -X DELETE
probe RT-T-04 scoped blocked "/s/$KEY_A/dicom-web/studies" -X POST --data-binary "@sample-data/CT_small.dcm"
# Ensure the denied DELETE really left the existing study intact.
admin "/studies/$FIRST_ID" >/dev/null
curl -sf -X POST "$API/session/revoke" -H "Content-Type: application/json" \
  -H "X-Coordinator-Key: ${GRADING_COORDINATOR_KEY}" \
  -d "{\"token\": \"$TOK_A\"}" >/dev/null
probe REVOKE scoped blocked "/s/$KEY_A/dicom-web/studies/$UID_A/metadata"
probe REVOKE scoped own-ok "/s/$KEY_B/dicom-web/studies/$UID_B/metadata"
echo "Legacy exposure: $legacy_gaps known read/search path(s) remain reachable (#195)."

echo
FINAL=$(finalize_code "$status" "$ASSERT_SCOPED")
if [ "$status" -gt 0 ] && [ "$FINAL" -eq 1 ]; then
  echo "RESULT: FAILED (--assert-scoped): $status gap(s) -- a session reached data it was not issued, or the scoped flow is broken."
elif [ "$status" -gt 0 ]; then
  echo "RESULT: $status gap(s) RECORDED above; exit 0 per the item-0 contract (run with --assert-scoped to fail on them). Known open state until callers switch + PR-B2."
else
  echo "RESULT: gated checks passed; legacy exposure is reported separately, not certified safe."
fi
exit "$FINAL"
