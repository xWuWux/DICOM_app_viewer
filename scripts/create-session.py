#!/usr/bin/env python3
"""
Mint a single-use, per-student Kasm Workspaces session URL for the DICOM viewer.

Field names/response shapes below are per Kasm's own developer API docs
(https://kasm.com/docs/latest/developers/developer_api.html), not guessed --
still worth re-confirming against your instance's version if something looks
off, since undocumented fields do shift between releases.

Requires, from the Kasm admin UI (Settings -> Developers -> Add API Key,
with the "Users Auth Session" and "User" permissions enabled on the key):
  KASM_API_KEY, KASM_API_KEY_SECRET
And the image_id of the registered "IP_CMC DICOM Viewer (MVP)" workspace
(visible in its URL in the admin UI, or via /api/public/get_images).

Usage:
  KASM_SERVER=https://localhost \
  KASM_API_KEY=... KASM_API_KEY_SECRET=... KASM_IMAGE_ID=... \
  python3 scripts/create-session.py --student-id STU_12345
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.error


def api_call(server: str, path: str, payload: dict, insecure: bool = False, fatal: bool = True) -> dict:
    url = f"{server.rstrip('/')}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    ctx = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        message = f"Kasm API error calling {path}: {e.code} {e.read().decode()}"
        if fatal:
            sys.exit(message)
        print(f"  (non-fatal) {message}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--session-id", default=None, help="defaults to a timestamp")
    parser.add_argument(
        "--insecure", action="store_true",
        help="skip TLS verification (e.g. Kasm's self-signed install cert)",
    )
    args = parser.parse_args()

    server = os.environ["KASM_SERVER"]
    api_key = os.environ["KASM_API_KEY"]
    api_key_secret = os.environ["KASM_API_KEY_SECRET"]
    image_id = os.environ["KASM_IMAGE_ID"]

    session_id = args.session_id or str(int(time.time()))

    # user_id is intentionally omitted: per Kasm's docs, request_kasm creates
    # a throwaway/anonymous user when it's left out -- exactly what we want
    # for a one-off per-student link, no pre-provisioned Kasm user needed.
    # STUDENT_ID/SESSION_ID land in the container's environment and are what
    # custom_startup.sh bakes into the watermark; ORTHANC_URL/VIEWER_URL
    # already have correct defaults baked into the image itself (see
    # docker/kasm-workspace/Dockerfile) so they're not overridden here.
    request_payload = {
        "api_key": api_key,
        "api_key_secret": api_key_secret,
        "image_id": image_id,
        "environment": {
            "STUDENT_ID": args.student_id,
            "SESSION_ID": session_id,
        },
    }

    created = api_call(server, "/api/public/request_kasm", request_payload, args.insecure)
    kasm_id = created.get("kasm_id")
    user_id = created.get("user_id")
    if not kasm_id:
        sys.exit(f"Unexpected response from request_kasm: {json.dumps(created, indent=2)}")

    # The link is already usable at this point (Kasm shows its own "starting"
    # screen while the container boots) -- this loop is just a best-effort
    # readiness confirmation, not a gate on handing out the link. A scoped
    # API key commonly lacks the separate "impersonate another user"
    # permission get_kasm_status needs for an anonymous/other user_id, so
    # failures here are expected and non-fatal.
    kasm = None
    for _ in range(30):
        status = api_call(
            server,
            "/api/public/get_kasm_status",
            {
                "api_key": api_key,
                "api_key_secret": api_key_secret,
                "kasm_id": kasm_id,
                "user_id": user_id,
            },
            args.insecure,
            fatal=False,
        )
        if status is None:
            print("  (skipping readiness polling)", file=sys.stderr)
            break
        kasm = status.get("kasm")
        if kasm and kasm.get("operational_status") == "running":
            break
        progress = status.get("operational_progress")
        print(f"  ...{status.get('operational_status', 'starting')} ({progress}%)" if progress is not None
              else f"  ...{status.get('operational_status', 'starting')}", file=sys.stderr)
        time.sleep(2)

    link = f"{server.rstrip('/')}{created['kasm_url']}"

    print(json.dumps({
        "student_id": args.student_id,
        "session_id": session_id,
        "kasm_id": kasm_id,
        "user_id": user_id,
        "ready": bool(kasm and kasm.get("operational_status") == "running"),
        "link": link,
    }, indent=2))


if __name__ == "__main__":
    main()
