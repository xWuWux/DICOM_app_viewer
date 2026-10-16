#!/usr/bin/env bash
# Uploads every *.dcm under a directory into the running Orthanc (REST), in
# parallel. For LOCAL, on-premise data only (issue #87): the studies received from
# the radiologist live outside the repository (Dokumentacja/DICOM_Images, git-
# ignored) and must stay on this host -- nothing here talks to anything but the
# Orthanc URL below. Unlike load-sample-studies.sh (public samples, ~10 files)
# this handles thousands of files: the upload step prints COUNTS only, never file
# names or DICOM values (the PHI gate below may name example files in its findings,
# owner decision 2026-10-16; it never prints values), and keeps the Orthanc password out of every process's argv
# (a temporary mode-0600 curl config, removed on exit).
#
#   ./scripts/load-local-studies.sh Dokumentacja/DICOM_Images [--jobs N]
#                             [--skip-list FILE] [--skip-phi-gate]
#                             [--allow-phi-tag TAG=REASON ...]
#
# PHI gate (issue #238 B1): every load is asserted against
# scripts/phi-gate.py FIRST -- files still carrying direct patient
# identifiers are refused before any byte reaches Orthanc (tag names and
# counts only are printed, never values). --allow-phi-tag tolerates ONE
# reject-tier tag with a required written reason; --skip-phi-gate bypasses
# the whole gate. Both are operator attestations, echoed to the log.
# Anonymisation itself is UPSTREAM of ingestion -- this script never fixes a
# file, only refuses it.
#
# --skip-list FILE: paths (relative to DIR, one per line) NOT to upload, e.g. the
# originals that scripts/dicom-patient-consistency.py --fix-out replaced by
# corrected copies (file names containing newlines are not supported).
# Files that are not *.dcm (e.g. Windows "<name>.dcm:Zone.Identifier") are
# skipped. Exit status is non-zero if any upload failed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${ORTHANC_PASSWORD:-}" ] && [ -f "$REPO_ROOT/.env" ]; then
  set -a; source "$REPO_ROOT/.env"; set +a
fi

DIR="${1:-}"
JOBS=4
SKIP_LIST=""
SKIP_PHI_GATE=0
ALLOW_PHI=()
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --jobs) JOBS="${2:?--jobs needs a number}"; shift 2 ;;
    --skip-list) SKIP_LIST="${2:?--skip-list needs a file}"; shift 2 ;;
    --skip-phi-gate) SKIP_PHI_GATE=1; shift ;;
    --allow-phi-tag) ALLOW_PHI+=("${2:?--allow-phi-tag needs TAG=REASON}"); shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -z "$SKIP_LIST" ] || [ -f "$SKIP_LIST" ] || { echo "--skip-list file does not exist" >&2; exit 2; }
DIR="${DIR%/}"
[ -n "$DIR" ] && [ -d "$DIR" ] || { echo "usage: $0 <directory with .dcm files> [--jobs N]" >&2; exit 2; }
case "$JOBS" in ''|*[!0-9]*|0) echo "--jobs must be a positive integer" >&2; exit 2;; esac

# Issue #238 B1: assertion-only anonymisation boundary. Anonymisation runs
# UPSTREAM of ingestion (owner decision 2026-10-15); nothing in this repo
# de-identifies, and this gate never "fixes" a file -- it REFUSES loads whose
# files still carry direct patient identifiers, before any byte reaches
# Orthanc. FAIL-CLOSED: no python3+pydicom means no load (this path loads
# real cohorts; loading them unasserted is exactly the failure B1 prevents).
# Values are never printed -- only tag names, counts and one example path.
if [ "$SKIP_PHI_GATE" = "1" ]; then
  echo "PHI GATE SKIPPED BY FLAG -- the upload proceeds UNASSERTED; the anonymity claim is yours, not the pipeline's." >&2
elif ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import pydicom" >/dev/null 2>&1; then
  echo "PHI gate needs python3 with pydicom (the operator venv that already runs build-cases-file.py). Refusing to load blind (fail-closed). --skip-phi-gate only if you personally own the anonymity claim." >&2
  exit 2
else
  GATE_ARGS=()
  for spec in ${ALLOW_PHI[@]+"${ALLOW_PHI[@]}"}; do GATE_ARGS+=(--allow-tag "$spec"); done
  [ -z "$SKIP_LIST" ] || GATE_ARGS+=(--exclude-file "$SKIP_LIST")
  python3 "$REPO_ROOT/scripts/phi-gate.py" "$DIR" ${GATE_ARGS[@]+"${GATE_ARGS[@]}"} || { echo "load REFUSED by phi-gate (tags above; values never shown) -- fix the source upstream" >&2; exit 1; }
fi

export ORTHANC_URL="${ORTHANC_URL:-http://localhost:8042}"
ORTHANC_USER="${ORTHANC_USER:-orthanc}"
ORTHANC_PASS="${ORTHANC_PASS:-${ORTHANC_PASSWORD:?Set ORTHANC_PASSWORD in .env -- see .env.example}}"

CURL_CONF="$(mktemp)"
trap 'rm -f "$CURL_CONF"' EXIT
chmod 600 "$CURL_CONF"
printf 'user = "%s:%s"\n' "$ORTHANC_USER" "$ORTHANC_PASS" > "$CURL_CONF"
export CURL_CONF

list_files() {
  if [ -n "$SKIP_LIST" ]; then
    find "$DIR" -type f -iname '*.dcm' -print | grep -vxF -f <(sed "s#^#$DIR/#" "$SKIP_LIST") | tr '\n' '\0' || true
  else
    find "$DIR" -type f -iname '*.dcm' -print0
  fi
}
total=$(list_files | tr -cd '\0' | wc -c)
[ "$total" -gt 0 ] || { echo "no .dcm files found" >&2; exit 2; }
echo "uploading $total DICOM files to $ORTHANC_URL with $JOBS parallel jobs (counts only; no names are printed)"

# One upload with retries (Orthanc, hammered by parallel clients, answers a share of
# requests with a reset/5xx; re-sending an instance is idempotent -- it is simply
# recognised as already stored). Prints "ok" or "fail:<http code or curl exit>".
upload_one() {
  local attempt code rc
  for attempt in 1 2 3 4; do
    code=$(curl -sS -K "$CURL_CONF" -X POST "$ORTHANC_URL/instances" \
             -H "Expect:" -H "Content-Type: application/dicom" --data-binary "@$1" \
             -o /dev/null -w '%{http_code}' 2>/dev/null) && rc=0 || rc=$?
    if [ "$rc" -eq 0 ] && [ "$code" = "200" ]; then echo ok; return 0; fi
    [ "$attempt" -lt 4 ] && sleep "$attempt"
  done
  if [ "$rc" -ne 0 ]; then echo "fail:curl$rc"; else echo "fail:http$code"; fi
}
export -f upload_one

results="$(list_files \
  | xargs -0 -P "$JOBS" -n 1 bash -c 'upload_one "$0"' \
  | awk -v total="$total" '{ n++; if ($1 != "ok") { f++; why[$1]++ } if (n % 500 == 0) printf "  ...%d/%d\n", n, total; }
                           END { printf "RESULT %d %d\n", n, f; for (k in why) printf "WHY %s %d\n", k, why[k] }')"
printf '%s\n' "$results" | { grep -v -e '^RESULT' -e '^WHY' || true; }
printf '%s\n' "$results" | { grep '^WHY' || true; } | sed 's/^WHY /  failure reason: /'
read -r _ done_n failed_n <<<"$(printf '%s\n' "$results" | grep '^RESULT')"
echo "done: $done_n uploaded, $failed_n failed"
if [ "${failed_n:-1}" -eq 0 ]; then
  echo "next: python3 scripts/verify-cases-in-orthanc.py <cases.json>  (counts-only cross-check that every case's study actually landed, issue #160 S1)"
fi
[ "${failed_n:-1}" -eq 0 ]
