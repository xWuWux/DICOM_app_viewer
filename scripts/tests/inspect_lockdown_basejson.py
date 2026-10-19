#!/usr/bin/env python3
"""Validate a built image's shipped Weasis base.json against the lockdown.

Reads base.json on stdin, checks every key patch-weasis-config.py must have
disabled (plus the blanked felix bundle), exits 1 on any mismatch. Extracted
from scripts/test-copy-lockdown.sh unchanged when issue #263 added the
Guacamole image as a second build to inspect -- both vendor images must ship
byte-identical lockdown state because both run the same patcher over the
same pinned Weasis .deb.
"""

import json
import sys

EXPECTED_DISABLED_KEYS = {
    "weasis.show.disclaimer",
    "weasis.export.dicom",
    "weasis.export.dicom.send",
    "weasis.import.dicom",
    "weasis.import.images",
    "weasis.import.dicom.qr",
}
EXPECTED_BLANKED_BUNDLE_KEY = "felix.auto.start.110"


def main() -> int:
    config = json.load(sys.stdin)
    by_code = {pref["code"]: pref["value"] for pref in config["weasisPreferences"]}

    failed = False
    for key in EXPECTED_DISABLED_KEYS:
        if by_code.get(key) != "false":
            print(
                f'FAIL: shipped base.json has {key} = {by_code.get(key)!r}, expected "false"',
                file=sys.stderr,
            )
            failed = True

    if by_code.get(EXPECTED_BLANKED_BUNDLE_KEY) != "":
        print(
            f"FAIL: shipped base.json has {EXPECTED_BLANKED_BUNDLE_KEY} = "
            f"{by_code.get(EXPECTED_BLANKED_BUNDLE_KEY)!r}, expected an empty string "
            "(DICOM send / Q-R / ISO-writer bundles would still load at runtime)",
            file=sys.stderr,
        )
        failed = True

    if failed:
        return 1
    print("shipped base.json matches every expected lockdown value.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
