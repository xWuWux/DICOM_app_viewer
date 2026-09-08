#!/usr/bin/env python3
"""
Mint a single-use, per-student Kasm Workspaces session URL for the DICOM viewer.

STATUS: template, not yet verified against a running Kasm install (Kasm isn't
deployed yet in this repo — see README.md "From MVP to the real thing"). Field
names below follow Kasm's public API pattern as of writing; confirm them
against your instance's own docs before relying on this
(https://kasmweb.com/docs/api.html, linked from CLAUDE.md).

Requires, from the Kasm admin UI (Access -> API Keys):
  KASM_API_KEY, KASM_API_KEY_SECRET
And the image_id of the registered "ipcmc/dicom-viewer:mvp" workspace image.

Usage:
  KASM_SERVER=https://kasm.internal \
  KASM_API_KEY=... KASM_API_KEY_SECRET=... KASM_IMAGE_ID=... \
  python3 scripts/create-session.py --student-id STU_12345
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error


def api_call(server: str, path: str, payload: dict) -> dict:
    url = f"{server.rstrip('/')}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Kasm API error calling {path}: {e.code} {e.read().decode()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--session-id", default=None, help="defaults to a timestamp")
    args = parser.parse_args()

    server = os.environ["KASM_SERVER"]
    api_key = os.environ["KASM_API_KEY"]
    api_key_secret = os.environ["KASM_API_KEY_SECRET"]
    image_id = os.environ["KASM_IMAGE_ID"]
    kasm_user_id = os.environ.get("KASM_USER_ID")  # a pre-provisioned Kasm user for this student

    session_id = args.session_id or str(int(time.time()))

    request_payload = {
        "api_key": api_key,
        "api_key_secret": api_key_secret,
        "image_id": image_id,
        "environment": {
            "STUDENT_ID": args.student_id,
            "SESSION_ID": session_id,
            "ORTHANC_URL": os.environ["ORTHANC_URL"],
        },
    }
    if kasm_user_id:
        request_payload["user_id"] = kasm_user_id

    created = api_call(server, "/api/public/request_kasm", request_payload)
    kasm_id = created.get("kasm_id")
    if not kasm_id:
        sys.exit(f"Unexpected response from request_kasm: {created}")

    # Poll until the container is actually running before handing out the link.
    status = created
    for _ in range(30):
        if status.get("kasm_status") == "running" or status.get("operational_status") == "ready":
            break
        time.sleep(2)
        status = api_call(
            server,
            "/api/public/get_kasm_status",
            {
                "api_key": api_key,
                "api_key_secret": api_key_secret,
                "kasm_id": kasm_id,
                "user_id": status.get("user_id"),
            },
        )

    print(json.dumps(status, indent=2))
    print(
        "\nIf `kasm_url` / `session_token` fields above didn't appear, check the "
        "response shape against https://kasmweb.com/docs/api.html for your Kasm "
        "version and adjust this script accordingly."
    )


if __name__ == "__main__":
    main()
