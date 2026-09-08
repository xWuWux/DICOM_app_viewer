#!/usr/bin/env bash
# Fetches larger public-domain teaching DICOM studies into sample-data/,
# from Orthanc's own official public demo server. These are NOT committed to
# git (see .gitignore) -- they're sizable (tens of MB) and the source is a
# stable, long-standing public resource, so re-fetching is cheaper than
# carrying the binaries in repo history forever. The two tiny pydicom
# fixtures in sample-data/ stay committed as-is; this is for bigger additions.
#
# Usage: ./scripts/fetch-public-samples.sh
set -euo pipefail

DEMO_SERVER="https://orthanc.uclouvain.be/demo"
DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sample-data"

fetch_patient() {
  local patient_id="$1" dest_name="$2" label="$3"
  local dest_dir="${DATA_DIR}/${dest_name}"
  if [ -d "$dest_dir" ] && [ -n "$(ls -A "$dest_dir" 2>/dev/null)" ]; then
    echo "${label}: already present at ${dest_dir}, skipping"
    return
  fi
  echo "Fetching ${label} (patient ${patient_id}) from ${DEMO_SERVER}..."
  mkdir -p "$dest_dir"
  local zip_path
  zip_path="$(mktemp --suffix=.zip)"
  curl -sSf --max-time 180 -o "$zip_path" "${DEMO_SERVER}/patients/${patient_id}/archive"
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$zip_path" "$dest_dir"
  rm -f "$zip_path"
  echo "  -> extracted to ${dest_dir}"
}

# 5Yp0E - BRAINIX: MRI cerebral study, 232 instances / 7 series / ~64MB.
# Public teaching dataset (classic OsiriX/Orthanc sample), already anonymized.
fetch_patient "16738bc3-e47ed42a-43ce044c-a3414a45-cb069bd0" "brainix" "BRAINIX (5Yp0E)"

echo "Done. Run scripts/load-sample-studies.sh to upload into Orthanc."
