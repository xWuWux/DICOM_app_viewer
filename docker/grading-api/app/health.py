"""Health semantics: liveness, readiness and dependency probes (issue #113).

Three questions, three endpoints, deliberately NOT the same one:

  GET /livez   -- "is the process running and answering?" No DB, no network,
                  no configuration. If this fails, the container is dead, full
                  stop. It is what a restart decision may safely be based on.
  GET /healthz -- unchanged, backwards compatible: "can the database be
                  opened and read?" Everything in the repo already uses it
                  (both compose healthchecks, smoke-test.sh,
                  test-degraded-modes.sh, test-visual-regression.sh), and,
                  critically, `viewer` has `depends_on: service_healthy` on
                  grading-api. Folding dependency probes into it would mean an
                  unavailable Orthanc leaves the grading panel unstartable --
                  a total outage out of a partial degradation. So it keeps its
                  narrow meaning on purpose; see docs/DEGRADED_MODES.md.
  GET /readyz  -- "would a student's request right now succeed?" Read AND
                  WRITE against the database, plus free space on the volume.
                  Dependencies (Orthanc) are reported as `degraded` in the body
                  without turning the status code red: images being unreadable
                  does not stop grading, and the two must be alertable
                  separately. For external monitoring (dead-man's switch), not
                  for container restart policy.

DoD 11's "distinguish 'no problems detected' from 'the monitoring system
itself has stopped'" cannot be answered by the monitored process at all -- a
dead monitor reports nothing. That half is the dead-man's switch in
docs/observability.md; /livez and /readyz are the half the process can own.

Probes are cached (`GRADING_HEALTH_CACHE_SECONDS`, default 15) because the
write probe takes a SQLite write lock: an unauthenticated endpoint that
writes on every call, on the rate-limited public edge, would be a
self-inflicted contention source -- exactly the failure mode busy_timeout
(#70) exists to bound.
"""

import os
import threading
import time
import urllib.error
import urllib.request

from . import db as db_module
from . import telemetry
from .logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_CACHE_SECONDS = 15
DEFAULT_MIN_FREE_BYTES = 100 * 1024 * 1024
DEFAULT_ORTHANC_URL = "http://orthanc:8042"
DEPENDENCY_TIMEOUT_SECONDS = 2.0

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "result": None}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("health_config_invalid", extra={"variable": name, "value": raw[:60]})
        return default


def cache_seconds() -> int:
    return _env_int("GRADING_HEALTH_CACHE_SECONDS", DEFAULT_CACHE_SECONDS)


def min_free_bytes() -> int:
    return _env_int("GRADING_STORAGE_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES)


def orthanc_url() -> str:
    """Empty string disables the dependency probe (`grading_dependency_up` is
    then absent from the snapshot rather than reported as 0 -- "not
    configured" and "down" must not look the same)."""
    raw = os.environ.get("GRADING_ORTHANC_URL", DEFAULT_ORTHANC_URL)
    return raw.strip()


def _error_name(exc: BaseException) -> str:
    """Client-facing error is the exception CLASS only. /readyz is
    unauthenticated, and a sqlite error message carries the database path --
    the same leak issue #62 closed on /healthz. The detail goes to the log."""
    return type(exc).__name__


def _check_database_read(conn) -> dict:
    started = time.monotonic()
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception as exc:  # noqa: BLE001 - every failure mode means "not ready"
        logger.exception("readyz_database_read_failed")
        return {"ok": False, "duration_ms": _elapsed_ms(started), "error": _error_name(exc)}
    return {"ok": True, "duration_ms": _elapsed_ms(started), "error": None}


def _check_database_write(conn) -> dict:
    """The signal /healthz structurally cannot provide: a database that reads
    fine but cannot be WRITTEN (read-only volume, full disk, a wedged writer
    lock) leaves every student's /submit as a 500 while the read-only probe
    stays green. Startup already refuses that state (#209); this is the same
    check repeated at runtime, where a volume can be remounted or filled."""
    started = time.monotonic()
    try:
        conn.execute(
            "INSERT INTO telemetry_probe (id, touched_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET touched_at = excluded.touched_at",
            (time.time(),),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("readyz_database_write_failed")
        if telemetry.is_db_lock_error(exc):
            # Same counter the 500 path increments: one signal for "SQLite made
            # us wait out busy_timeout", however it was noticed.
            telemetry.record_db_lock_error()
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - a connection that cannot roll back is already reported above
            pass
        return {"ok": False, "duration_ms": _elapsed_ms(started), "error": _error_name(exc)}
    return {"ok": True, "duration_ms": _elapsed_ms(started), "error": None}


def _check_storage() -> dict:
    directory = os.path.dirname(os.path.abspath(db_module.DB_PATH)) or "."
    threshold = min_free_bytes()
    try:
        stats = os.statvfs(directory)
    except OSError as exc:
        logger.exception("readyz_storage_statvfs_failed")
        return {"ok": False, "free_bytes": None, "min_free_bytes": threshold, "error": _error_name(exc)}
    free = stats.f_bavail * stats.f_frsize
    telemetry.set_gauge(telemetry.VOLUME_FREE_BYTES, free)
    return {"ok": free >= threshold, "free_bytes": free, "min_free_bytes": threshold, "error": None}


def _probe_orthanc(url: str) -> dict:
    """Reachability, not health: Orthanc has AuthenticationEnabled=true and this
    service holds no Orthanc credentials, so ANY HTTP answer (401/403
    included) proves the dependency is up and serving. Only a connection
    error or timeout is "down" -- which is exactly the failure students
    experience (blank viewer frame)."""
    started = time.monotonic()
    request = urllib.request.Request(url.rstrip("/") + "/system", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=DEPENDENCY_TIMEOUT_SECONDS) as response:
            response.read(0)
            status_code = response.status
        ok, error, detail = True, None, f"http_{status_code}"
    except urllib.error.HTTPError as exc:
        ok, error, detail = True, None, f"http_{exc.code}"
    except Exception as exc:  # noqa: BLE001 - URLError, timeouts, bad TLS: all "down"
        ok, error, detail = False, _error_name(exc), None
    duration_ms = _elapsed_ms(started)
    telemetry.set_gauge(telemetry.DEPENDENCY_UP, 1 if ok else 0, {"dependency": "orthanc"})
    telemetry.set_gauge(telemetry.DEPENDENCY_PROBE_DURATION_MS, duration_ms, {"dependency": "orthanc"})
    result = {"ok": ok, "duration_ms": duration_ms, "error": error}
    if detail is not None:
        result["detail"] = detail
    return result


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def _evaluate() -> dict:
    checks: dict = {}
    conn = None
    try:
        conn = db_module.get_connection()
        checks["database_read"] = _check_database_read(conn)
        checks["database_write"] = _check_database_write(conn)
    except Exception as exc:  # noqa: BLE001 - cannot even open a connection
        logger.exception("readyz_database_unavailable")
        checks["database_read"] = {"ok": False, "duration_ms": 0.0, "error": _error_name(exc)}
        checks["database_write"] = {"ok": False, "duration_ms": 0.0, "error": _error_name(exc)}
    finally:
        if conn is not None:
            conn.close()

    checks["storage"] = _check_storage()

    url = orthanc_url()
    if url:
        checks["dependency:orthanc"] = _probe_orthanc(url)
    else:
        checks["dependency:orthanc"] = {"ok": None, "skipped": True}

    core_ok = all(checks[name]["ok"] for name in ("database_read", "database_write", "storage"))
    dependency_ok = all(entry["ok"] is not False for name, entry in checks.items() if name.startswith("dependency:"))
    if not core_ok:
        status = "not_ready"
    elif not dependency_ok:
        status = "degraded"
    else:
        status = "ready"

    now = time.time()
    telemetry.set_gauge(telemetry.READYZ_OK, 1 if core_ok else 0)
    telemetry.set_gauge(telemetry.READYZ_LAST_CHECK_UNIXTIME, now)
    return {"status": status, "checked_at": now, "checks": checks}


def readiness(force: bool = False) -> tuple[dict, bool]:
    """(payload, http_ok). Cached for GRADING_HEALTH_CACHE_SECONDS; a cached
    answer is marked `cached: true` so a monitor never mistakes a stale
    verdict for a fresh probe."""
    ttl = cache_seconds()
    now = time.time()
    with _lock:
        cached = _cache["result"]
        if not force and cached is not None and ttl > 0 and (now - _cache["at"]) < ttl:
            payload = {**cached, "cached": True, "age_seconds": round(now - _cache["at"], 2)}
            return payload, payload["status"] != "not_ready"
        payload = _evaluate()
    with _lock:
        _cache["at"] = time.time()
        _cache["result"] = payload
    return {**payload, "cached": False, "age_seconds": 0.0}, payload["status"] != "not_ready"


def liveness() -> dict:
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - telemetry.PROCESS_START_UNIXTIME_VALUE, 3),
    }


def reset_cache() -> None:
    """Tests only."""
    with _lock:
        _cache["at"] = 0.0
        _cache["result"] = None
