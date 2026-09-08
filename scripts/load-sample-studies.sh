#!/usr/bin/env bash
# Uploads the public-domain sample DICOM files in sample-data/ (recursively --
# covers both the small pydicom fixtures at the top level and anything pulled
# in by fetch-public-samples.sh, e.g. sample-data/brainix/) into the running
# Orthanc container via its REST API. All of this is public teaching/test
# data, already anonymized — NOT real patient data. Real ingestion still needs
# the anonymization pipeline described in CLAUDE.md.
set -euo pipefail

ORTHANC_URL="${ORTHANC_URL:-http://localhost:8042}"
ORTHANC_USER="${ORTHANC_USER:-orthanc}"
ORTHANC_PASS="${ORTHANC_PASS:-CHANGE_ME_ORTHANC_PASSWORD}"
DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../sample-data" && pwd)"

while IFS= read -r -d '' f; do
  echo "Uploading ${f#"$DATA_DIR"/}..."
  curl -sSf -u "${ORTHANC_USER}:${ORTHANC_PASS}" \
    -X POST "${ORTHANC_URL}/instances" \
    --data-binary "@${f}" \
    -H "Expect:" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  ->", d.get("Status"), d.get("ID"))'
done < <(find "$DATA_DIR" -type f \( -iname "*.dcm" -o -iname "IM*" \) -print0)

echo "Done. Browse studies at ${ORTHANC_URL}/ (Orthanc Explorer 2)."
