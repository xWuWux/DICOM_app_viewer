#!/usr/bin/env python3
"""
Mints a FRESH link to a still-running Kasm session (issue #159).

Why this script exists: after a logout from the Kasm account the old link
`#/connect/kasm/<id>/<user>/<jwt>` is dead for good -- its row is gone from
Kasm's session_tokens table and the browser loops on login_saml with bad
credentials (roughly 17 failed calls/s from one stuck tab). The session
container itself is usually still running; the public get_kasm_status API
returns a fresh kasm_url (a new session_token_id) for it, given the API key.
This script is the coordinator-only wrapper around exactly that call.

Two measured facts about that call (Kasm 1.19.0 CE, the issue #159 freeze
matrix; the step log is the ADR 0001 appendix once PR #252 merges), both
encoded below and pinned by tests:
  - It answers in two different shapes. Running/starting: kasm_url at the TOP
    LEVEL plus a nested kasm object. Paused: exactly one key, a top-level
    operational_status, and no link at all. So "no link" is NOT the same
    diagnosis as "session gone" -- for a frozen session it means resume first.
  - Every call mints a new session_token row and a fresh JWT. That is why this
    script calls status exactly once per open and never retries: a polling loop
    would leave one minted link per attempt behind.

Security rules this script exists to enforce (issue #159's acceptance
criteria):
  - The fresh link goes to STDOUT and nowhere else. stdout is the operator's
    handoff channel; stderr carries no link material.
  - Error paths never echo the response body -- on this API even a body that
    looks like an error can carry a fresh kasm_url. The JWT class of leak
    that the 2026-09-22 transcript incident produced is exactly what this
    guards against.
  - The Kasm API key/secret never appear in any message (the request object
    is never printed, only the exception TYPE is named).

Usage:
  python3 scripts/reissue-session-link.py --kasm-id 42 --user-id 7 > /tmp/link.txt

Environment:
  KASM_SERVER, KASM_API_KEY, KASM_API_KEY_SECRET  (same variables as create-session.py)
  TLS_CA_BUNDLE   optional CA for a self-signed Kasm (or --insecure, lab only)

Exit status: 0 = fresh link printed to stdout; 1 = no link (reason on stderr,
no secrets); 2 = usage or configuration error; 3 = the session is PAUSED, so no
link exists yet and the caller has to resume it first. 1 and 3 are kept apart on
purpose: "gone" and "frozen" need different automated responses, and making a
caller read stderr prose to tell them apart is how incident handling gets built
wrong.

Note: the printed link embeds a JWT -- treat /tmp/link.txt as a credential:
0600 file or direct paste, never commit, never CI-log.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


class ReissueError(Exception):
    """A failure phrased for an operator: one line, no secrets, no response body."""


def build_ssl_context(ca_bundle=None, insecure=False):
    ctx = ssl.create_default_context()
    if ca_bundle:
        ctx.load_verify_locations(ca_bundle)
    if insecure:
        print("WARNING: --insecure disables certificate verification; "
              "use TLS_CA_BUNDLE instead on any shared host.", file=sys.stderr)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get_status(opener, server, key, secret, kasm_id, user_id, ctx):
    """POST get_kasm_status, return the parsed body. The body may carry a fresh
    kasm_url, so on error ONLY the status code or exception type is reported --
    the body and the request (it holds the keys) are never echoed."""
    req = urllib.request.Request(
        server.rstrip("/") + "/api/public/get_kasm_status",
        data=json.dumps({"api_key": key, "api_key_secret": secret,
                         "kasm_id": kasm_id, "user_id": user_id}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # Measured on Kasm 1.19.0 CE: a key that may not impersonate the session's
            # user gets `403 {"error_message": "API Key (name) is not authorized to
            # impersonate a user."}` from this endpoint (permissions 50 and 100 together
            # were still not enough). Naming that cause is the difference between an
            # operator fixing the key and an operator hunting for a dead session.
            raise ReissueError(f"get_kasm_status refused with HTTP {e.code}; for this endpoint "
                               "401/403 usually means the API key may not impersonate the "
                               "session's user_id (body not echoed)") from None
        raise ReissueError(f"get_kasm_status refused with HTTP {e.code} (body not echoed)") from None
    except urllib.error.URLError as e:
        raise ReissueError(f"get_kasm_status connection error ({getattr(e, 'reason', e)})") from None
    except (TimeoutError, ConnectionError, ssl.SSLError) as e:
        raise ReissueError(f"get_kasm_status connection error ({type(e).__name__})") from None
    except ValueError:
        raise ReissueError("get_kasm_status returned a non-JSON body (not echoed)") from None


def fresh_link(status, server):
    """-> (link or None, operational_status). Both measured answer shapes are read.
    Running/resumed answers have top_keys ['current_time','kasm','kasm_url']: the link
    is top-level and the state lives only inside the nested kasm object. A paused
    answer has exactly one key, a top-level operational_status, and no kasm object and
    no link at all (#159 freeze matrix, Kasm 1.19.0 CE).

    So: link top-level first with the nested one as a fallback; state nested first,
    because the measured running answer has no top-level state at all and an outer
    field must never shadow the container's own state. Reading state from the nested
    object only (what this function used to do) turned a paused session into
    "answered without a kasm_url / the session may be gone", the wrong diagnosis for a
    session frozen on purpose -- there the action is resume, not re-mint or destroy. A
    session that is not running yet still has a usable link -- Kasm shows its own
    starting screen -- so state is a note, not an error."""
    kasm = status.get("kasm") or {}
    url = status.get("kasm_url") or kasm.get("kasm_url")
    state = kasm.get("operational_status") or status.get("operational_status") or "unknown"
    if not url:
        return None, state
    return server.rstrip("/") + url, state


def main(argv=None, opener=None):
    ap = argparse.ArgumentParser(description="Fresh link to a running Kasm session (issue #159).")
    ap.add_argument("--kasm-id", required=True)
    ap.add_argument("--user-id", required=True,
                    help="the user_id create-session.py returned")
    ap.add_argument("--ca-bundle", default=os.environ.get("TLS_CA_BUNDLE") or None)
    ap.add_argument("--insecure", action="store_true",
                    help="self-signed Kasm certificate (local lab only)")
    args = ap.parse_args(argv)

    try:
        server = os.environ["KASM_SERVER"]
        key = os.environ["KASM_API_KEY"]
        secret = os.environ["KASM_API_KEY_SECRET"]
    except KeyError as e:
        print(f"REFUSED: missing environment variable {e.args[0]} "
              "(same variables as create-session.py).", file=sys.stderr)
        return 2

    opener = opener or urllib.request.urlopen
    ctx = build_ssl_context(args.ca_bundle, args.insecure)
    try:
        status = get_status(opener, server, key, secret, args.kasm_id, args.user_id, ctx)
    except ReissueError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    link, note = fresh_link(status, server)
    if link is None:
        if note == "paused":
            # Measured: a paused session answers without any kasm_url. Saying
            # "may be gone" here would push an operator to destroy a session that
            # is holding a student's place deliberately. Exit 3, not 1: a paused
            # session is resumable, a gone one is not.
            print(f"PAUSED: session {args.kasm_id} is paused, and Kasm issues no link for a "
                  "paused session -- there is nothing to re-issue until it runs again. Resume "
                  "it first (the Kasm admin UI does this; the API call was not verified in the "
                  "lab, and pause/resume under a key without the matching permission answers "
                  "403), then re-run this script.", file=sys.stderr)
            return 3
        print(f"FAILED: get_kasm_status answered without a kasm_url "
              f"(operational_status={note!r}); the session may be gone -- end it and re-mint "
              "instead, see docs/runbooks/kasm-session-lifecycle.md", file=sys.stderr)
        return 1
    if note not in ("running", "starting"):
        print(f"WARNING: session operational_status={note!r} AND a link were returned together. "
              "That combination was never observed in the #159 matrix (a paused session returned "
              "no link at all), so the link is printed but may well not open.", file=sys.stderr)
    print(link)
    return 0


if __name__ == "__main__":
    sys.exit(main())
