#!/usr/bin/env bash
# Uploads every *.dcm under a directory into the running Orthanc (REST), in
# parallel. For LOCAL, on-premise data only (issue #87): the studies received from
# the radiologist live outside the repository (Dokumentacja/DICOM_Images, git-
# ignored) and must stay on this host -- nothing here talks to anything but the
# Orthanc URL below. Unlike load-sample-studies.sh (public samples, ~10 files)
# this handles thousands of files: it prints COUNTS only, never file names or
# DICOM values, and keeps the Orthanc password out of every process's argv
# (a temporary mode-0600 curl config, removed on exit).
#
#   ./scripts/load-local-studies.sh Dokumentacja/DICOM_Images [--jobs N]
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
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --jobs) JOBS="${2:?--jobs needs a number}"; shift 2 ;;
    --skip-list) SKIP_LIST="${2:?--skip-list needs a file}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -z "$SKIP_LIST" ] || [ -f "$SKIP_LIST" ] || { echo "--skip-list file does not exist" >&2; exit 2; }
DIR="${DIR%/}"
[ -n "$DIR" ] && [ -d "$DIR" ] || { echo "usage: $0 <directory with .dcm files> [--jobs N]" >&2; exit 2; }
case "$JOBS" in ''|*[!0-9]*|0) echo "--jobs must be a positive integer" >&2; exit 2;; esac

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
[ "${failed_n:-1}" -eq 0 ]
