#!/usr/bin/env python3
"""Freeze-matrix probe for Kasm (spike tool, issue #159 -- NOT product code).

What this is
------------
The driver behind `docs/adr/0001-kasm-session-freeze-mechanism.md`. It walks ONE
session through request -> pause -> resume -> stop -> destroy against a
disposable Kasm install, samples the container's memory at every state, reads
the keepalive/expiration dates from Kasm's database, and writes a REDACTED JSON
log. `docs/adr/0001-appendix-freeze-matrix.md` was built from such a log.

What this is not
----------------
Not product code, not wired into CI, not a pattern to copy. Two of the ADR's
conclusions were only reachable because this existed and printed what actually
came back instead of what was expected, so it is kept -- but everything here
assumes a throw-away install owned by the person running it.

Credentials
-----------
From the environment only, never on the command line (they would land in a
shell history and in the process list):

    KASM_SERVER           e.g. https://localhost
    KASM_ADMIN_USERNAME   admin login, used for pause/resume/stop/destroy
    KASM_ADMIN_PASSWORD
    KASM_IMAGE_ID         workspace image to launch

The API key pair for the PUBLIC create is read too, when present, and the F
half is skipped with a message when it is not:

    KASM_API_KEY, KASM_API_KEY_SECRET

Nothing here prints a token, a link or a secret; the log keeps fingerprints.
A lab install's admin password usually has to be reset by writing a hash into
its database first -- that is a disposable-lab shortcut, never something to do
on a host with real users (see the ADR's Reproducibility note).

    KASM_SERVER=https://localhost KASM_ADMIN_USERNAME=admin@kasm.local \
    KASM_ADMIN_PASSWORD=... KASM_IMAGE_ID=<uuid> \
        python3 scripts/spike/kasm-freeze-probe.py --insecure --out /tmp/matrix.json

TLS verification is ON by default; `--insecure` is the explicit opt-out for a
self-signed install cert, `--ca-bundle` the private-CA alternative (the same
pair of flags as `scripts/create-session.py`, and the same rule that they
contradict each other).

Memory figures come from `docker stats`, which reports cgroup usage INCLUDING
PAGE CACHE, not strict RSS. Every number that left this tool says so.
"""
import argparse
import hashlib
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Response fields that must never reach the log, whatever endpoint returned
# them. Identifiers are replaced by short fingerprints instead: enough to ask
# "is this the same session?" without storing the answer.
SECRET_FIELDS = frozenset({
    "token", "api_key", "api_key_secret", "session_token", "kasm_url",
    "connect_url", "connection_info", "connection_credential", "password",
    "pw_hash", "crypt_password", "crypt_salt", "salt", "view_only_token",
    "api_token", "share_id", "secret",
})
ID_FIELDS = frozenset({"container_id", "user_id", "kasm_id", "image_id", "api_id"})

# A token that is deliberately not a JWT, for the "does the link's JWT actually
# authenticate?" control. Low entropy and no eyJ... prefix on purpose: gitleaks
# (security-scan stage) flags any high-entropy JWT-shaped literal as a generic API
# key, and because that stage scans the whole committed history, such a literal
# cannot be removed later -- the commit that introduced it has to be rewritten, and
# until then it fails the security stage of EVERY open PR (see the header of
# scripts/security-scan.sh). Kasm rejects this string the same way either way.
GARBAGE_TOKEN = "not-a-jwt-not-a-token"

MEM_UNITS = {"KiB": 1.0 / 1024, "MiB": 1.0, "GiB": 1024.0, "TiB": 1024.0 * 1024,
             "kB": 1.0 / 1024, "MB": 1.0 / 1024, "GB": 1.0}


def fingerprint(value):
    """Stable 12-char tag for a secret: lets two responses be compared for
    equality without either value being stored.

    A missing value is deliberately NOT the same tag as an empty one ("absent
    link" and "blank link" have to stay distinguishable, and a field that
    disappeared is exactly the thing test F turned on).
    """
    text = "\x00absent" if value is None else str(value)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def redact(obj, _depth=0):
    """Copy of an API response safe to commit: secrets and identifiers become
    fingerprints, everything else is kept, because the shape of a response is
    the finding. Lists are capped, deep nesting is not followed forever."""
    if _depth > 6:
        return "..."
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in SECRET_FIELDS or key in ID_FIELDS:
                if isinstance(value, (dict, list)):
                    out[key + "_shape"] = type(value).__name__
                else:
                    out[key + "_fp"] = fingerprint(value)
            else:
                out[key] = redact(value, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item, _depth + 1) for item in list(obj)[:10]]
    if isinstance(obj, str) and len(obj) > 200:
        return obj[:200] + "..."
    return obj


def parse_docker_stats(line):
    """One `docker stats --no-stream` row -> {"name","mem_mib","cpu_pct"}.

    Returns None when the line is not a stats row. `mem_mib` is the USAGE half
    of the MEMUSAGE column (cgroup usage including page cache, not RSS).
    """
    fields = [f.strip() for f in line.split("\t")]
    if len(fields) < 3:
        return None
    name, mem_usage, cpu_raw = fields[0], fields[1], fields[2]
    mem = None
    used = mem_usage.split("/")[0].strip()
    for unit, factor in MEM_UNITS.items():
        if used.endswith(unit):
            try:
                mem = round(float(used[: -len(unit)].strip()) * factor, 3)
            except ValueError:
                mem = None
            break
    cpu = None
    try:
        cpu = float(cpu_raw.rstrip("%").strip())
    except ValueError:
        pass
    return {"name": fingerprint(name), "mem_mib": mem, "cpu_pct": cpu}


def read_status(body):
    """What get_kasm_status actually said, across BOTH response shapes.

    Kasm answers this endpoint two different ways and the difference is the
    whole reason test F was wrong for a day (see ADR 0001): a running session
    answers {"current_time", "kasm", "kasm_url"} with operational_status inside
    `kasm`, while a paused session answers exactly {"operational_status"} and
    carries NO link. Reading only kasm.operational_status reports a paused
    session as "no status", and reading only kasm.kasm_url reports a working
    link as absent.
    """
    if not isinstance(body, dict):
        return {"ok": False, "reason": "not-an-object", "raw": str(body)[:120]}
    inner = body.get("kasm") if isinstance(body.get("kasm"), dict) else {}
    return {
        "ok": True,
        "top_keys": sorted(body.keys()),
        "operational_status": inner.get("operational_status") or body.get("operational_status"),
        "kasm_url_present": "kasm_url" in body,
        "kasm_url_fp": fingerprint(body.get("kasm_url")) if "kasm_url" in body else None,
        "has_kasm_object": bool(inner),
    }


def db(sql):
    """Read-only query against the lab's Kasm database through docker exec.

    The database is the reason this tool can pair a running and a paused
    reading at all: a paused session reports no container_id over the API, so
    `docker stats` can only be aimed at it by taking the id from here.
    """
    proc = subprocess.run(
        ["docker", "exec", "kasm_db", "psql", "-U", "kasmapp", "-d", "kasm", "-t", "-A", "-c", sql],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def docker_stats(container_id):
    proc = subprocess.run(
        ["docker", "stats", "--no-stream", "--format",
         "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}", container_id],
        capture_output=True, text=True,
    )
    return parse_docker_stats(proc.stdout.strip())


class KasmProbe:
    """Minimal Kasm API client.

    Two URL spaces matter and mixing them up produced wrong conclusions twice:
    `/api/admin/<method>` for admin-only methods, `/api/<method>` for the
    client methods a logged-in user may call (`pause_kasm`, `resume_kasm`,
    `stop_kasm`, `destroy_kasm`), and `/api/public/<request_kasm>` for the
    student-link path. In every case the caller's credentials go IN THE JSON
    BODY -- Kasm's `Authenticated` decorator reads `username`/`token` (or
    `api_key`/`api_key_secret`) from the payload, not from headers.
    """

    def __init__(self, server, username, password, context):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.token = None
        self.context = context
        self.calls = 0

    def call(self, path, payload=None):
        body = dict(payload or {})
        self.calls += 1
        request = urllib.request.Request(
            self.server + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=180) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return error.code, {"raw": raw[:200].decode("utf-8", "replace")}
        except (urllib.error.URLError, TimeoutError) as error:
            return 0, {"error": str(error)[:200]}

    def login(self):
        status, body = self.call("/api/authenticate",
                                 {"username": self.username, "password": self.password})
        self.token = body.get("token") if status == 200 and isinstance(body, dict) else None
        return bool(self.token)

    def as_admin(self, method, **fields):
        if not self.token:
            raise RuntimeError("not logged in")
        return self.call("/api/admin/" + method, dict(username=self.username, token=self.token, **fields))

    def as_client(self, method, **fields):
        if not self.token:
            raise RuntimeError("not logged in")
        return self.call("/api/" + method, dict(username=self.username, token=self.token, **fields))


def _wait_for_status(kasm_id, wanted, attempts=30, delay=2):
    """Poll the database, not the API, for a state transition."""
    for attempt in range(attempts):
        if db(f"select operational_status from kasms where kasm_id='{kasm_id}'") == wanted:
            return attempt
        time.sleep(delay)
    return None


def _dates(kasm_id):
    return db("select coalesce(expiration_date::text,'NULL') || ' | ' || "
              f"coalesce(keepalive_date::text,'NULL') from kasms where kasm_id='{kasm_id}'")


def _record(steps, name, **fields):
    steps.append(dict(step=name, at=time.strftime("%H:%M:%S"), **fields))


def run_d_e_matrix(probe, image_id, soak_seconds=80):
    """Tests D (idle timer while paused) and E (memory per state), paired on
    ONE session, plus the stopped-session disk footprint. Session is owned by
    the logged-in admin, which is exactly what the production flow does NOT do
    -- see the A's note about anonymous users in the ADR."""
    steps = []
    status, created = probe.as_client("request_kasm", image_id=image_id)
    if not isinstance(created, dict) or "kasm_id" not in created:
        _record(steps, "request_kasm_FAILED", status=status, response=redact(created))
        return steps
    kasm_id = created["kasm_id"]
    _record(steps, "request_kasm", status=status, response_shape=redact(created))
    _wait_for_status(kasm_id, "running")
    container_id = db(f"select container_id from kasms where kasm_id='{kasm_id}'")
    _record(steps, "container_id_from_db", present=bool(container_id))
    if not container_id:
        _record(steps, "abort_no_container", kasm_id_fingerprint=fingerprint(kasm_id))
        return steps

    _record(steps, "E_running", sample=docker_stats(container_id))
    status, body = probe.as_client("pause_kasm", kasm_id=kasm_id)
    _record(steps, "pause_kasm", status=status, response=redact(body))
    _wait_for_status(kasm_id, "paused", attempts=20, delay=2)
    _record(steps, "E_paused_just_after", sample=docker_stats(container_id))

    before = _dates(kasm_id)
    time.sleep(soak_seconds)
    after = _dates(kasm_id)
    _record(steps, "D_paused_dates", before=before, after=after, unchanged=(before == after))
    _record(steps, "E_paused_soaked", sample=docker_stats(container_id),
            soak_seconds=soak_seconds)

    status, body = probe.as_client("resume_kasm", kasm_id=kasm_id)
    _record(steps, "resume_kasm", status=status, response=redact(body))
    _wait_for_status(kasm_id, "running", attempts=30, delay=2)
    _record(steps, "E_resumed", sample=docker_stats(container_id))
    _record(steps, "D_after_resume", dates=_dates(kasm_id),
            note="a changed expiration_date here does NOT distinguish a full "
                 "re-arm from a shift by the paused duration; see ADR 0001 test D")

    status, body = probe.as_client("stop_kasm", kasm_id=kasm_id)
    _record(steps, "stop_kasm", status=status, response=redact(body))
    time.sleep(6)
    layer = subprocess.run(
        ["docker", "ps", "-s", "-a", "--filter", f"id={container_id}", "--format", "{{.Size}}"],
        capture_output=True, text=True,
    )
    _record(steps, "stopped_writable_layer", size=(layer.stdout or layer.stderr).strip()[:120])

    status, body = probe.as_client("destroy_kasm", kasm_id=kasm_id)
    _record(steps, "destroy_kasm", status=status)
    _record(steps, "left_behind", kasms=db("select count(*) from kasms"),
            anonymous_users=db("select count(*) from users where username like 'anon%'"),
            session_token_rows=db("select count(*) from session_tokens"))
    return steps


def run_f_matrix(probe, image_id, api_key, api_secret):
    """Test F: what the student's link is, on the path that creates it."""
    steps = []
    session = {"api_key": api_key, "api_key_secret": api_secret}
    status, created = probe.call("/api/public/request_kasm",
                                 dict(session, image_id=image_id,
                                      environment={"STUDENT_ID": "spike-f"}))
    if not isinstance(created, dict) or not created.get("kasm_url"):
        _record(steps, "public_request_kasm_FAILED", status=status, response=redact(created))
        return steps
    url = created["kasm_url"]
    try:
        kasm_id, user_id, link_jwt = url.split("/#/connect/kasm/")[1].split("?")[0].split("/")[:3]
    except IndexError:
        _record(steps, "unexpected_url_shape", keys=sorted(created.keys()))
        return steps
    _record(steps, "public_request_kasm", status=status, keys=sorted(created.keys()),
            url_fingerprint=fingerprint(url),
            jwt_is_the_session_token=(link_jwt == (created.get("session_token") or "")))

    def status_via_link(label, token_value):
        code, body = probe.call("/api/public/get_kasm_status",
                                {"token": token_value, "username": created.get("user_id") or user_id,
                                 "kasm_id": kasm_id})
        _record(steps, "F_" + label, status=code, **read_status(body))
        return code, body

    status_via_link("running", link_jwt)
    probe.as_client("pause_kasm", kasm_id=kasm_id)
    _wait_for_status(kasm_id, "paused", attempts=20, delay=2)
    status_via_link("while_paused", link_jwt)
    probe.as_client("resume_kasm", kasm_id=kasm_id)
    _wait_for_status(kasm_id, "running", attempts=30, delay=2)
    status_via_link("after_resume", link_jwt)

    code, body = probe.call("/api/public/get_kasm_status",
                            {"token": GARBAGE_TOKEN, "username": user_id, "kasm_id": kasm_id})
    _record(steps, "F_control_garbage_token", status=code, message=str(body)[:80])
    code, body = probe.call("/api/public/get_kasm_status",
                            {"username": user_id, "kasm_id": kasm_id})
    _record(steps, "F_control_no_token", status=code, message=str(body)[:80])
    code, body = probe.call("/api/public/get_kasm_status",
                            dict(session, user_id=user_id, kasm_id=kasm_id))
    _record(steps, "F_control_api_key_not_jwt", status=code, message=str(body)[:80])

    code, body = probe.call("/api/public/pause_kasm", dict(session, kasm_id=kasm_id, user_id=user_id))
    _record(steps, "F_pause_with_key_only", status=code, message=str(body)[:120])

    probe.as_client("resume_kasm", kasm_id=kasm_id)
    time.sleep(8)
    status, body = probe.as_client("destroy_kasm", kasm_id=kasm_id)
    _record(steps, "destroy_kasm", status=status)
    return steps


def build_context(ca_bundle=None, insecure=False):
    context = ssl.create_default_context()
    if ca_bundle:
        context.load_verify_locations(cafile=ca_bundle)
    elif insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set in the environment (see this file's docstring)")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kasm freeze-matrix probe (spike tool, issue #159)")
    parser.add_argument("--server", default=os.environ.get("KASM_SERVER") or None,
                        help="Kasm base URL (env KASM_SERVER)")
    parser.add_argument("--image-id", default=os.environ.get("KASM_IMAGE_ID") or None,
                        help="workspace image to launch (env KASM_IMAGE_ID); "
                             "defaults to the first enabled image")
    parser.add_argument("--out", default="/tmp/kasm-freeze-matrix.json",
                        help="where to write the REDACTED log")
    parser.add_argument("--soak", type=int, default=80,
                        help="seconds between the two idle-timer reads while paused")
    parser.add_argument("--skip-f", action="store_true",
                        help="do not run the public-endpoint half even if an API key is present")
    parser.add_argument("--ca-bundle", default=os.environ.get("TLS_CA_BUNDLE") or None,
                        help="PEM with the CA that signed the install certificate (env TLS_CA_BUNDLE)")
    parser.add_argument("--insecure", action="store_true",
                        help="skip TLS verification; for a self-signed lab cert only")
    args = parser.parse_args(argv)

    if args.insecure and args.ca_bundle:
        parser.error("--insecure and --ca-bundle contradict each other; prefer --ca-bundle")
    if args.ca_bundle and not os.path.isfile(args.ca_bundle):
        parser.error(f"--ca-bundle: no such file: {args.ca_bundle}")

    server = args.server or _require("KASM_SERVER")
    probe = KasmProbe(server, _require("KASM_ADMIN_USERNAME"), _require("KASM_ADMIN_PASSWORD"),
                      build_context(args.ca_bundle, args.insecure))
    if not probe.login():
        raise SystemExit("admin login failed (no token). Credentials wrong, or Kasm not answering "
                         f"at {server}")

    image_id = args.image_id
    if not image_id:
        status, images = probe.as_admin("get_images")
        enabled = [i for i in (images.get("images") or []) if i.get("enabled")]
        if not enabled:
            raise SystemExit(f"no enabled workspace image found (get_images -> {status})")
        image_id = enabled[0]["image_id"]

    api_key = os.environ.get("KASM_API_KEY", "").strip()
    api_secret = os.environ.get("KASM_API_KEY_SECRET", "").strip()
    log = {
        "tool": "scripts/spike/kasm-freeze-probe.py",
        "purpose": "freeze matrix for ADR 0001 / issue #159",
        "server": server,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "memory_caveat": "mem_mib is docker stats cgroup usage including page cache, NOT RSS",
        "redaction": "no token, link, password or raw identifier is stored; see SECRET_FIELDS",
        "image_fingerprint": fingerprint(image_id),
        "database_reachable": bool(db("select 1")),
        "steps": run_d_e_matrix(probe, image_id, soak_seconds=args.soak),
    }
    if api_key and api_secret and not args.skip_f:
        log["steps"] += run_f_matrix(probe, image_id, api_key, api_secret)
    elif not args.skip_f:
        log["f_skipped"] = "KASM_API_KEY / KASM_API_KEY_SECRET not set; the student-link half " \
                           "needs a key with the User permission (permission id 100)"

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(log, handle, indent=2, sort_keys=False)
        handle.write("\n")
    for step in log["steps"]:
        print(" | ".join(f"{k}={v}" for k, v in step.items() if k not in ("step", "at")))
    print(f"\nwrote {args.out} ({len(log['steps'])} steps, {probe.calls} API calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
