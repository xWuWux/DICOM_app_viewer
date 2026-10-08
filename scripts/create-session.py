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

Also mints a grading-api session token (see docker/grading-api/app/main.py's
POST /session) bound to this student_id -- this script is the only thing
that should ever be able to do that, hence GRADING_COORDINATOR_KEY: without
it, any client could mint its own token for any student_id and defeat the
whole point (README.md flagged this as a real, previously-unfixed gap
before this script closed it). Needs network access to `viewer`'s /api/
proxy -- GRADING_API_URL, default assumes this script runs on the same
Docker host as the stack (see README.md's own "everything on one host"
assumption); point it elsewhere for the separate-Proxmox-host deployment.

Issue #104, in one paragraph: the token used to be minted first and then
abandoned if the Kasm call failed, leaving a live, usable credential with no
session attached to it, and `--insecure` swapped in
`ssl._create_unverified_context()`, which silently disabled verification for
whatever credentials this script sends. Both are fixed here: TLS is verified
by default (`--ca-bundle` for a private CA; `--insecure` remains only as an
explicit, loudly-warned-about escape hatch for a self-signed lab box) and any
failure after the mint revokes that token again through POST
/api/session/revoke before exiting.

Usage:
  KASM_SERVER=https://localhost \
  KASM_API_KEY=... KASM_API_KEY_SECRET=... KASM_IMAGE_ID=... \
  GRADING_COORDINATOR_KEY=... \
  python3 scripts/create-session.py --student-id STU_12345

  # Kasm on a private CA (a certbot LE chain needs neither of these):
  python3 scripts/create-session.py --student-id STU_12345 --ca-bundle /etc/kasm/ca.pem
  # or the same thing without repeating the flag: export TLS_CA_BUNDLE=/etc/kasm/ca.pem
"""
import argparse
import ipaddress
import json
import os
import signal
import ssl
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

# Never longer than this (issue #104's compensation must not become another
# unbounded-error-text path, the class of bug fixed in #93/#62 for the server
# side of the same protocol).
_MAX_ERROR_BODY_CHARS = 500


class ApiError(RuntimeError):
    """A fatal API failure. Raised rather than sys.exit'd straight from
    api_call(), because exiting there would jump straight over main()'s
    compensation block and leave the minted token live -- which is the exact
    bug this script's issue #104 fix is about.

    `ambiguous` records whether the server's answer is actually known: a 4xx
    came back, so the request was refused and nothing was created; a timeout,
    a refused connection or a 5xx says nothing about what the server did with
    it. main()'s ambiguous-mint hint is gated on this (review nit N2)."""

    def __init__(self, message: str, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


def _interrupt(signum, frame):
    """SIGTERM/SIGHUP -> KeyboardInterrupt, so the compensation in main()
    actually runs (review nit on issue #104, CR w4:p1). The default action for
    both is "die silently", which orphaned a token exactly like Ctrl-C did --
    CI job cancellation and a closed terminal are the ordinary ways this script
    gets killed in practice, not Ctrl-C.

    SIGKILL and os._exit() stay uncoverable; the token's own TTL
    (GRADING_TOKEN_TTL_SECONDS, capped at 7 days by app/config.py) is the
    backstop, which is why README.md names the TTL rather than this handler as
    the last line of defense."""
    raise KeyboardInterrupt()


def install_termination_handlers() -> None:
    """Only ever called from main() -- importing this module must never install
    handlers as a side effect (a test or a future import would inherit them).
    ValueError from a non-main thread is deliberately swallowed: no handler is
    better than a crash for a caller that cannot have one.

    An inherited SIG_IGN is left exactly as it is (review nit N1): that is what
    `nohup` and `trap '' HUP` mean, and overriding it would turn a run that used
    to survive its terminal closing into one that aborts mid-flight. Ignoring a
    signal is its caller's deliberate choice; terminating on one is the default
    this handler exists to improve on."""
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            if signal.getsignal(sig) == signal.SIG_IGN:
                continue
            signal.signal(sig, _interrupt)
        except ValueError:
            pass


def build_ssl_context(ca_bundle: str = None, insecure: bool = False) -> ssl.SSLContext:
    """One context for both endpoints (grading-api and Kasm), built once.

    Verification is ON by default and there is deliberately no env var that
    turns it off -- `--insecure` has to be typed. Issue #104's TLS half is
    about the default, not about removing the escape hatch: Kasm's own
    installer ships a self-signed certificate (docs/PROXMOX_DEPLOYMENT.md's
    "Transport security"), so an operator following LOCAL_SETUP_GUIDE.md
    needs a way to get to a real cert gradually; what must not be possible is
    *defaulting* into no verification.
    """
    if insecure:
        ctx = ssl._create_unverified_context()
        print(
            "WARNING: --insecure -- TLS certificate and hostname verification are DISABLED for "
            "every request this script makes, including KASM_API_KEY_SECRET and GRADING_TOKEN. "
            "Only acceptable against a self-signed lab certificate you control; for a private CA, "
            "use --ca-bundle /path/to/ca.pem instead. See docs/PROXMOX_DEPLOYMENT.md.",
            file=sys.stderr,
        )
        return ctx
    # Appended to the system trust store, NOT used instead of it: Kasm and the
    # grading-api proxy are routinely two different CAs (a private one for the
    # Kasm gateway, LE for the viewer), and `create_default_context(cafile=...)`
    # would replace the whole store and break the other host -- a footgun
    # review nit caught on issue #104. check_hostname/CERT_REQUIRED are
    # create_default_context()'s defaults, i.e. verification is what you get
    # unless someone types --insecure.
    ctx = ssl.create_default_context()
    if ca_bundle:
        # ssl.SSLError here is left to propagate: main() turns it into
        # parser.error, the same shape as the --ca-bundle isfile check above it
        # (review nit N6). It can never carry a secret -- no request has been
        # made and no token exists at this point.
        ctx.load_verify_locations(cafile=ca_bundle)
    return ctx


def api_call(server: str, path: str, payload: dict, ctx: ssl.SSLContext = None, fatal: bool = True,
             headers: dict = None, *, label: str) -> dict:
    # `label` is required and keyword-only on purpose. It used to default to
    # "API", and the two Kasm call sites never passed one, so a Kasm failure
    # printed "API error calling /api/public/request_kasm" -- PR #134's review
    # (nit 1) caught the loss of diagnosability. A call site that forgets the
    # label should now fail as a TypeError while the test runs, not print a
    # vaguer message in front of an operator.
    url = f"{server.rstrip('/')}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        message = f"{label} error calling {path}: {e.code} {e.read().decode()[:_MAX_ERROR_BODY_CHARS]}"
        if fatal:
            # A 4xx is a definite refusal -- nothing was created. A 5xx is not:
            # the server may have committed before it fell over, which is
            # exactly what the ambiguous-mint hint is for (review nit N2).
            raise ApiError(message, ambiguous=e.code >= 500) from e
        print(f"  (non-fatal) {message}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        hint = ""
        if "CERTIFICATE_VERIFY" in str(reason).upper() or isinstance(reason, ssl.SSLCertVerificationError):
            # The temptation this addresses is typing --insecure to make the
            # error go away; say what the actual fix is instead (issue #104).
            hint = (" -- certificate verification failed: point --ca-bundle (or TLS_CA_BUNDLE) at "
                    "the CA that signed this instance's certificate rather than adding --insecure.")
        message = f"{label} connection error calling {path}: {reason}{hint}"
        if fatal:
            # Never got an answer at all: the request may or may not have been
            # handled. Ambiguous by definition.
            raise ApiError(message, ambiguous=True) from e
        print(f"  (non-fatal) {message}", file=sys.stderr)
        return None
    except (TimeoutError, ConnectionError, ssl.SSLError) as e:
        # urlopen wraps the failures it knows about in URLError, but a
        # socket-level timeout or reset raised while READING the response body
        # arrives here raw -- the CR probe on PR #134 hit exactly this as a read
        # timeout against request_kasm: the compensation had already run
        # correctly, and the operator still got a naked Python traceback and
        # exit 1. Same ambiguity as above (the request was sent; its outcome is
        # unknown), so the message carries ambiguous=True and stays one line.
        message = f"{label} connection error calling {path}: {type(e).__name__}: {e}"
        if fatal:
            raise ApiError(message, ambiguous=True) from e
        print(f"  (non-fatal) {message}", file=sys.stderr)
        return None
    except ValueError as e:
        # json.loads of a 200 whose body is not JSON at all -- the realistic
        # shape is viewer's nginx answering with its own HTML error page. A
        # bare json.JSONDecodeError traceback told the operator nothing about
        # which call went wrong; and this is ambiguous too: the server did
        # answer, but this script cannot tell what it said.
        # str(JSONDecodeError) names a parse position, not the document; still
        # capped, same rule as the HTTP error text above.
        message = f"{label} returned a non-JSON body calling {path}: {str(e)[:_MAX_ERROR_BODY_CHARS]}"
        if fatal:
            raise ApiError(message, ambiguous=True) from e
        print(f"  (non-fatal) {message}", file=sys.stderr)
        return None


def revoke_token(grading_api_url: str, coordinator_key: str, ctx: ssl.SSLContext, token: str,
                 student_id: str, session_id: str) -> None:
    """Undo a minted token (see docker/grading-api/app/main.py's POST
    /session/revoke). Best-effort by necessity -- if this call also fails,
    the mint we are trying to undo already happened, so all that is left is
    telling the operator precisely how to undo it by hand.

    Never prints the token itself: this script's own output goes into shell
    history, CI logs and chat paste-aways, and 2026-09-22 already produced
    one incident where long-lived credentials ended up in a committed
    transcript (see .gitignore's transcript exclusions). A leaked session
    token is the same class of leak as a leaked Kasm key, just scoped to one
    student. student_id/session_id are printed instead -- they identify the
    row to fix without being a credential."""
    try:
        resp = api_call(
            grading_api_url, "/api/session/revoke", {"token": token}, ctx,
            headers={"X-Coordinator-Key": coordinator_key},
            fatal=False, label="grading-api",
        )
    except BaseException as e:  # noqa: BLE001 -- see below
        # BaseException, not Exception: a second Ctrl-C landing *during* the
        # compensation call used to escape this function with a traceback and
        # the token still live -- i.e. the one path this function exists to
        # handle. Swallowing it costs the operator a stuck Ctrl-C for up to one
        # 15s request; the alternative is a silent orphan.
        resp = None
        print(f"  (non-fatal) grading-api revoke raised: {e!r}", file=sys.stderr)
    if resp and resp.get("revoked"):
        print(f"  (compensated) revoked the grading-api token for {student_id}/{session_id}",
              file=sys.stderr)
        return
    print(
        f"  !! grading-api token for {student_id}/{session_id} was NOT revoked and stays usable "
        "until it expires. Undo it now by minting a link for that same student_id (POST /session "
        "deletes any token that student_id already had), or call POST /api/session/revoke with "
        '{"student_id": "' + student_id + '"}.',
        file=sys.stderr,
    )


def warn_if_coordinator_key_travels_in_clear(grading_api_url: str) -> None:
    """The shared secret that mints (and now revokes) every student's token is
    sent as a header. Over http:// to a loopback address that never leaves the
    box; over http:// to anything else it crosses a network in clear text and
    anyone on path can mint tokens for any student_id.

    Warning, not refusal, because the documented default deployment IS
    http://localhost:8080/ (README.md, LOCAL_SETUP_GUIDE.md) and refusing
    there would break every existing invocation; non-loopback http has never
    been a documented setup, so nobody relying on it gets broken by the noise
    either. (review nit on issue #104, CR w4:p1.)"""
    parsed = urllib.parse.urlparse(grading_api_url)
    # No .lower() needed: urlparse normalizes the scheme to lowercase by
    # definition, so HTTP:// is covered (pinned by the parametrized test).
    if parsed.scheme != "http":
        return
    host = parsed.hostname or ""
    if host in ("localhost", "", "0.0.0.0", "::"):
        # 0.0.0.0/:: are not loopback strictly speaking, but as a destination
        # from this host they mean "this box", and warning there would be noise
        # on the documented default setup.
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        # a hostname, not a literal -- judged below
        pass
    print(
        f"WARNING: GRADING_API_URL is plain http to {host!r} -- GRADING_COORDINATOR_KEY and "
        "every minted session token cross the network unencrypted. Put grading-api behind TLS "
        "(https://) or keep it loopback-only.",
        file=sys.stderr,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--session-id", default=None, help="defaults to a timestamp")
    parser.add_argument(
        "--ca-bundle", default=os.environ.get("TLS_CA_BUNDLE") or None,
        help="PEM file with the CA that signed Kasm's / grading-api's certificate "
             "(env TLS_CA_BUNDLE). Verification is on with or without it.",
    )
    parser.add_argument(
        "--insecure", action="store_true",
        help="skip TLS verification (e.g. Kasm's self-signed install cert). Verify nothing, "
             "trust anyone -- prefer --ca-bundle.",
    )
    args = parser.parse_args()

    if args.insecure and args.ca_bundle:
        # --insecure would silently win and the operator would believe their
        # CA was in use. Fail loudly instead (same philosophy as
        # docker/grading-api/app/config.py, issue #97).
        parser.error("--insecure and --ca-bundle contradict each other; pick --ca-bundle")
    if args.ca_bundle and not os.path.isfile(args.ca_bundle):
        parser.error(f"--ca-bundle: no such file: {args.ca_bundle}")

    server = os.environ["KASM_SERVER"]
    api_key = os.environ["KASM_API_KEY"]
    api_key_secret = os.environ["KASM_API_KEY_SECRET"]
    image_id = os.environ["KASM_IMAGE_ID"]
    coordinator_key = os.environ["GRADING_COORDINATOR_KEY"]
    grading_api_url = os.environ.get("GRADING_API_URL", "http://localhost:8080/")

    session_id = args.session_id or str(int(time.time()))
    try:
        ctx = build_ssl_context(args.ca_bundle, args.insecure)
    except ssl.SSLError as e:
        # Same shape as the isfile check above: a bad --ca-bundle is an
        # operator typo, not a traceback (review nit N6). Nothing has been sent
        # and no token exists yet, so this message can carry the file path
        # safely.
        parser.error(f"--ca-bundle: {args.ca_bundle} is not a usable PEM CA bundle ({e.strerror or e})")
    install_termination_handlers()
    warn_if_coordinator_key_travels_in_clear(grading_api_url)

    # Issue #104: these two stay None until they actually exist, and main()'s
    # except-block below reads their current values to decide what still has
    # to be undone.
    grading_token = None
    kasm_id = None
    mint_attempted = False

    def compensate(exc):
        """Undo whatever was already created. Only reachable from the failure
        path, so it is deliberately cheap and never raises."""
        if grading_token and not kasm_id:
            # No Kasm session ever took possession of this token, so revoking
            # is both safe and the whole point. Once kasm_id exists the token
            # is already inside a real container's environment -- revoking
            # there would break a session the student may be joining.
            revoke_token(grading_api_url, coordinator_key, ctx, grading_token,
                         args.student_id, session_id)
        elif mint_attempted and not grading_token and getattr(exc, "ambiguous", True):
            # The mint request went out and its outcome is unknown: POST /session
            # deletes that student_id's previous token before inserting a new one,
            # so either the student just lost a live link, or a token exists whose
            # value nobody holds. Revoking by student_id from here would be wrong
            # in the first case (it would finish destroying a session nothing
            # failed to create), so this stays a loud hint for a human with the
            # context this script does not have.
            #
            # Gated on the failure being ambiguous (review nit N2): a 401/403 from
            # the coordinator gate is the most common error this script sees and is
            # a definite "nothing was minted" -- printing a `!!` token-hunting hint
            # for it would train the operator to ignore the hint.
            print(
                f"  !! grading-api POST /session for {args.student_id}/{session_id} did not "
                "return a token, so this script cannot undo it. Check whether a token exists "
                'and revoke it: POST /api/session/revoke {"student_id": "' + args.student_id + '"}.',
                file=sys.stderr,
            )

    try:
        # The only place a grading-api token gets minted (see that service's
        # POST /session) -- fatal on failure, not best-effort like the Kasm
        # readiness poll below: a session without a token can't do anything
        # useful once it's open, so there's no point handing out a link for one.
        # Set before the call, not after: the whole point is that a mint whose
        # response never made it back is exactly the case that needs flagging.
        mint_attempted = True
        session_created = api_call(
            grading_api_url, "/api/session",
            {"student_id": args.student_id, "session_id": session_id},
            ctx,
            headers={"X-Coordinator-Key": coordinator_key},
            label="grading-api",
        )
        grading_token = session_created.get("token")
        if not grading_token:
            # Raised rather than indexed as session_created["token"]: an
            # unexpected response shape used to raise a naked KeyError, whose
            # traceback printed the whole response body -- and a future shape
            # that returns the token alongside an error field would then be
            # dumped straight into the terminal/log (issue #93's leak class,
            # applied to this side of the protocol).
            raise ApiError("grading-api POST /session returned no token", ambiguous=True)

        # user_id is intentionally omitted: per Kasm's docs, request_kasm creates
        # a throwaway/anonymous user when it's left out -- exactly what we want
        # for a one-off per-student link, no pre-provisioned Kasm user needed.
        # STUDENT_ID/SESSION_ID land in the container's environment purely for
        # display (the watermark text) -- GRADING_TOKEN is what custom_startup.sh
        # actually uses to talk to grading-api now, never the raw student_id.
        # ORTHANC_URL/VIEWER_URL already have correct defaults baked into the
        # image itself (see docker/kasm-workspace/Dockerfile) so they're not
        # overridden here.
        #
        # This is also why the order cannot simply be inverted to "Kasm first,
        # then token" (issue #104's first acceptance-criteria branch): the
        # token's value has to exist before request_kasm, because it is handed
        # to the container as environment on that very call. The compensation
        # branch of that criterion is what revoke_token() above implements.
        request_payload = {
            "api_key": api_key,
            "api_key_secret": api_key_secret,
            "image_id": image_id,
            "environment": {
                "STUDENT_ID": args.student_id,
                "SESSION_ID": session_id,
                "GRADING_TOKEN": grading_token,
            },
        }

        created = api_call(server, "/api/public/request_kasm", request_payload, ctx,
                            label="Kasm API")
        kasm_id = created.get("kasm_id")
        user_id = created.get("user_id")
        if not kasm_id:
            raise ApiError(
                "Unexpected response from request_kasm: "
                # Same cap as api_call's own error text: a fat 200 body must not
                # reach a terminal / CI log / shell history in full.
                + json.dumps(created)[:_MAX_ERROR_BODY_CHARS]
            )

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
                ctx,
                fatal=False,
                label="Kasm API",
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
    except BaseException as exc:
        # BaseException, not Exception: the failure that used to orphan a token
        # includes Ctrl-C during the Kasm call, which raises KeyboardInterrupt
        # and would sail straight past an `except Exception`.
        compensate(exc)
        if isinstance(exc, ApiError):
            sys.exit(str(exc))
        if isinstance(exc, KeyboardInterrupt):
            sys.exit("interrupted")
        raise


if __name__ == "__main__":
    main()
