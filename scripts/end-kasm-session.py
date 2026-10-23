#!/usr/bin/env python3
"""
Ends a Kasm session created by scripts/create-session.py: destroys the container
AND its Kasm record (issue #159), then revokes the student's grading-api token
through the SAME revoke the Guacamole teardown uses.

Why this script exists (issue #159): logging out of Kasm's side panel destroys
nothing. Only the group keepalive timer (idle 3600 s, then delete) ever removed
the container, so the CLAUDE.md rule "containers destroyed on logout" was never
enforced on the Kasm path. This makes "end this session" an explicit,
verifiable coordinator action; Kasm's own panel still offers no working end
button. See docs/runbooks/kasm-session-lifecycle.md.

Steps, all reported; any failed step yields exit 1 (partial state is loud):
  1. destroy_kasm   Kasm public API; removes the container and the record.
  2. verify_gone    get_kasm_status must no longer show a live session. With a
                    scoped key lacking the "impersonate" permission a destroyed
                    and an invisible kasm answer the same way, and the report
                    says so instead of guessing.
  3. revoke_token   grading-api POST /session/revoke by student_id, through the
                    shared library scripts/guacamole_session.py -- the "revoke
                    is common, only the host teardown differs" shape from the
                    scope note on #159. A revoked token closes the second door:
                    even a stale link cannot reach the grading API.

Usage:
  python3 scripts/end-kasm-session.py --kasm-id 42 --user-id 7 --student-id STU_12345

Environment:
  KASM_SERVER, KASM_API_KEY, KASM_API_KEY_SECRET  (same variables as create-session.py)
  TLS_CA_BUNDLE          optional CA for a self-signed Kasm (or --insecure, lab only)
  GRADING_API_URL + GRADING_COORDINATOR_KEY       optional: with them the token is
                         revoked too; without them the step is reported skipped and
                         the token expires on its own TTL. --student-id is required
                         whenever revoking can run.

Exit status: 0 = every executed step succeeded (a skipped revoke is fine);
1 = a step failed; 2 = usage or configuration error (nothing was touched).

The Kasm keys never appear in any message this script prints. get_kasm_status
response bodies are never echoed either: a body carries a fresh kasm_url, and a
kasm_url carries a JWT.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from secret_config import coordinator_key as read_coordinator_key
import guacamole_session as gs_mod  # noqa: E402  (shared revoke, see docstring)


class EndError(Exception):
    """A Kasm-side failure phrased for an operator: one line, no secrets, no body."""


def build_ssl_context(ca_bundle=None, insecure=False):
    ctx = ssl.create_default_context()
    if ca_bundle:
        ctx.load_verify_locations(ca_bundle)
    if insecure:
        print("WARNING: --insecure disables certificate verification for the Kasm calls; "
              "use TLS_CA_BUNDLE instead on any shared host.", file=sys.stderr)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def kasm_api(opener, server, path, payload, ctx):
    """POST JSON to Kasm and return the parsed body. Error text carries the status
    code or the exception TYPE only -- never the body (a body can carry a fresh
    kasm_url/JWT) and never the request (it holds the api_key/api_key_secret)."""
    req = urllib.request.Request(
        server.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = EndError(f"{path}: refused with HTTP {e.code}")
        # check_gone decides 404 vs 403/401 vs 5xx on the code itself, so it has to
        # survive the re-raise. Nothing else about the response is kept: a body can
        # carry a fresh kasm_url.
        err.http_code = e.code
        raise err from None
    except urllib.error.URLError as e:
        raise EndError(f"{path}: connection error ({getattr(e, 'reason', e)})") from None
    except (TimeoutError, ConnectionError, ssl.SSLError) as e:
        raise EndError(f"{path}: connection error ({type(e).__name__})") from None
    except ValueError:
        raise EndError(f"{path}: non-JSON response body (not echoed)") from None


def destroy_kasm(opener, server, key, secret, kasm_id, user_id, ctx):
    body = kasm_api(opener, server, "/api/public/destroy_kasm",
                    {"api_key": key, "api_key_secret": secret,
                     "kasm_id": kasm_id, "user_id": user_id}, ctx)
    # Kasm answers destroy_kasm with {"success": true} on this deployment; an
    # unknown shape still counts as "answered" -- the verify step below decides.
    if body.get("success") is False:
        raise EndError("/api/public/destroy_kasm: answered success=false")
    return "ok"


def check_gone(opener, server, key, secret, kasm_id, user_id, ctx):
    """-> (gone: bool, detail: str). True ONLY when something positively says the
    session is not there: an HTTP 404 or an explicit deleted state.

    Measured on Kasm 1.19.0 CE (lab, 2026-10-16, key created for the measurement and
    granted permission 100 "User"): with a key that may not impersonate the session's
    user, BOTH destroy_kasm and get_kasm_status answer
    `403 {"error_message": "API Key (name) is not authorized to impersonate a user."}`
    while the container is verifiably alive (docker ps -a present, kasms row running).
    An authorization answer is therefore not an absence answer. The previous behaviour
    folded that 403 into "gone or invisible to this key" and counted it as verified,
    so with a scoped key the verify step did not verify anything and still reported a
    successful end. Anything that cannot confirm (401/403, 5xx, transport errors, a 200
    that carries no state) is now reported as NOT confirmed: the script exits 1 and the
    operator looks at the admin UI instead of trusting a green line.

    Whether Kasm answers 404 for a destroyed session was NOT measured: reaching that
    answer needs a key holding the impersonate permission, which this lab does not have
    (permissions 50 and 100 together still answered 403). 404 is kept as gone because
    it is the conventional reading; everything unconfirmed errs toward exit 1.
    """
    try:
        status = kasm_api(opener, server, "/api/public/get_kasm_status",
                          {"api_key": key, "api_key_secret": secret,
                           "kasm_id": kasm_id, "user_id": user_id}, ctx)
    except EndError as e:
        code = getattr(e, "http_code", None)
        if code == 404:
            return True, f"gone (404): {e}"
        if code in (401, 403):
            return False, (f"NOT CONFIRMED, this key cannot see the session ({e}). Kasm answers "
                           "403 when the key may not impersonate the session's user_id; the "
                           "session may well be alive. Check the admin UI or re-run with a key "
                           "that holds that permission.")
        return False, f"NOT CONFIRMED, status check failed ({e}); the session may be alive, re-run"
    kasm = status.get("kasm") or {}
    # Nested first: the measured running/resumed answer has top_keys
    # ['current_time','kasm','kasm_url'] and carries the state only inside kasm, while
    # the measured paused answer has exactly one key, a top-level operational_status,
    # and no kasm object at all (#159 freeze matrix). Nested-first also means an
    # outer wrapper field can never shadow the container's own state.
    state = kasm.get("operational_status") or status.get("operational_status")
    if state == "deleted":
        return True, "operational_status=deleted"
    if state in (None, ""):
        return False, ("NOT CONFIRMED: the answer carries no operational_status at either level; "
                       "destroyed and an unknown response shape look the same here")
    if state == "running":
        return False, "still running"
    if state in ("paused", "stopped"):
        # A paused session answers without a nested kasm object, and a stopped one
        # still keeps its container on disk (91.8 MB measured while stopped).
        return False, f"still present: operational_status={state}, not destroyed"
    # Anything else (e.g. "starting" right after destroy) is a teardown still in
    # flight; report not-gone and let the operator re-run -- destroying an
    # already gone kasm fails loudly rather than silently.
    return False, f"operational_status={state}"


def revoke_for_student(student_id, grading_api_url, coordinator_key):
    """Shared library call (scripts/guacamole_session.py). Raises APIError/URLError
    on connectivity problems; bool means 'revoked' vs 'nothing-to-revoke'."""
    return gs_mod.revoke_token_for_student(
        gs_mod.real_opener, grading_api_url, coordinator_key, student_id)


def main(argv=None, opener=None):
    ap = argparse.ArgumentParser(description="End one Kasm session for good (issue #159).")
    ap.add_argument("--kasm-id", required=True)
    ap.add_argument("--user-id", required=True,
                    help="the user_id create-session.py returned (destroy_kasm needs it)")
    ap.add_argument("--student-id",
                    help="required when GRADING_API_URL + GRADING_COORDINATOR_KEY are set")
    ap.add_argument("--ca-bundle", default=os.environ.get("TLS_CA_BUNDLE") or None)
    ap.add_argument("--insecure", action="store_true",
                    help="self-signed Kasm certificate (local lab only)")
    args = ap.parse_args(argv)

    grading_api_url = os.environ.get("GRADING_API_URL", "")
    coordinator_key = read_coordinator_key(required=False)
    revoking = bool(grading_api_url and coordinator_key)
    if revoking and not args.student_id:
        print("REFUSED: --student-id is required when grading-api revocation is configured "
              "(otherwise the token would stay usable until TTL with nobody tracking it).",
              file=sys.stderr)
        return 2

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

    print(f"Ending Kasm session {args.kasm_id} (user {args.user_id}) ...")
    failed = False

    try:
        result = destroy_kasm(opener, server, key, secret, args.kasm_id, args.user_id, ctx)
        print(f"  destroy_kasm: {result}")
    except EndError as e:
        print(f"  FAILED destroy_kasm: {e}", file=sys.stderr)
        print("  Nothing else was attempted; the session is untouched or half-torn -- "
              "retry, and check the Kasm admin UI if a retry also fails.", file=sys.stderr)
        return 1

    gone, detail = check_gone(opener, server, key, secret, args.kasm_id, args.user_id, ctx)
    if gone:
        print(f"  verify_gone: OK ({detail})")
    else:
        print(f"  FAILED verify_gone: {detail}", file=sys.stderr)
        failed = True

    if revoking:
        try:
            revoked = revoke_for_student(args.student_id, grading_api_url, coordinator_key)
            print(f"  revoke_token: {'revoked' if revoked else 'nothing to revoke'} "
                  f"for {args.student_id}")
        except BaseException as e:  # noqa: BLE001
            # Same stance as create-session.py's compensation: the kasm destroy
            # already happened; what helps is the exact manual command, never a
            # traceback -- and a Ctrl-C here must not escape with the token live
            # and no instructions on screen.
            print(f"  FAILED revoke_token: {type(e).__name__}: {e}", file=sys.stderr)
            print(f'  !! the grading token for {args.student_id} may still be usable until it '
                  'expires. Undo it by minting a link for the same student_id (POST /session '
                  'deletes the previous one), or call POST /api/session/revoke with '
                  f'{{"student_id": "{args.student_id}"}} on grading-api.', file=sys.stderr)
            failed = True
    else:
        print("  NOTE revoke_token: skipped (GRADING_API_URL/GRADING_COORDINATOR_KEY unset); "
              "the token expires on its own TTL.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
