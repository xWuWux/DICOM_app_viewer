#!/usr/bin/env bash
# Uploads the public-domain sample DICOM files in sample-data/ into the running
# Orthanc container via its REST API. These are pydicom's own test fixtures
# (MIT-licensed, synthetic/anonymized test data) — NOT real patient data.
# Real ingestion still needs the anonymization pipeline described in CLAUDE.md.
set -euo pipefail

ORTHANC_URL="${ORTHANC_URL:-http://localhost:8080/orthanc}"
ORTHANC_USER="${ORTHANC_USER:-orthanc}"
ORTHANC_PASS="${ORTHANC_PASS:-CHANGE_ME_ORTHANC_PASSWORD}"
DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../sample-data" && pwd)"

for f in "$DATA_DIR"/*.dcm; do
  echo "Uploading $(basename "$f")..."
  curl -sSf -u "${ORTHANC_USER}:${ORTHANC_PASS}" \
    -X POST "${ORTHANC_URL}/instances" \
    --data-binary "@${f}" \
    -H "Expect:" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  ->", d.get("Status"), d.get("ID"))'
done

echo "Done. Browse studies at ${ORTHANC_URL}/ (Orthanc Explorer 2)."
