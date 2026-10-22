"""In-process telemetry registry and the signal snapshot (issue #113).

Why a hand-rolled registry and not `prometheus_client`: the Tier 4 scope for
this ticket is "a documented telemetry contract + the signals that DoD
10/11/87/88/104/112 ask for", explicitly NOT a Prometheus deployment. The
image's `requirements.txt` is a two-line attack surface on purpose (see
requirements-dev.txt's own note) and there is no Prometheus server in the
stack to scrape anything. A dict of counters costs nothing, is trivially
testable, and -- crucially -- emits the *same metric names* a Prometheus
migration would use (`_total` suffix on counters, `_ms` on histograms,
labels as a flat map), so moving to `prometheus_client` later is an
exporter change, not a rename and not a re-write of every alert.

Single-process only by design: the Dockerfile runs uvicorn with no
`--workers`, so in-process counters are complete and correct. With more
than one worker each process would report its own partial totals -- the
documented limitation in docs/observability.md, not something this module
silently gets wrong.

Counters reset on restart. That is stated in the contract and is why
`grading_telemetry_last_success_unixtime` exists: an alert must be able to
tell "zero errors" from "the process restarted a second ago", and a
dead-man's switch must be able to tell "no data" from "no problems".
"""

import os
import sqlite3
import threading
import time
from collections import defaultdict

from .logging_config import get_logger

logger = get_logger(__name__)

# ---- Signal names: the single source of truth for docs/observability.md ----
# tests/test_observability_contract.py parses the doc's signal table and
# fails when the two drift apart. Keep the names stable: they are the alert
# contract, not an implementation detail.
DB_INTEGRITY_OK = "grading_db_integrity_ok"
REQUESTS_TOTAL = "grading_requests_total"
REQUEST_DURATION_MS = "grading_request_duration_ms"
SESSIONS_ACTIVE = "grading_sessions_active"
SUBMISSIONS_TOTAL = "grading_submissions_total"
DB_LOCK_ERRORS_TOTAL = "grading_db_lock_errors_total"
DB_WRITE_DURATION_MS = "grading_db_write_duration_ms"
DB_SIZE_BYTES = "grading_db_size_bytes"
VOLUME_FREE_BYTES = "grading_volume_free_bytes"
DEPENDENCY_UP = "grading_dependency_up"
DEPENDENCY_PROBE_DURATION_MS = "grading_dependency_probe_duration_ms"
READYZ_OK = "grading_readyz_ok"
READYZ_LAST_CHECK_UNIXTIME = "grading_readyz_last_check_unixtime"
TELEMETRY_LAST_SUCCESS_UNIXTIME = "grading_telemetry_last_success_unixtime"
PROCESS_START_UNIXTIME = "grading_process_start_unixtime"
LAST_SUBMISSION_UNIXTIME = "grading_last_submission_unixtime"
LAST_SESSION_MINT_UNIXTIME = "grading_last_session_mint_unixtime"

# type/unit/labels per signal. `labels` is an ordered tuple because it is
# rendered in that order everywhere (JSON samples and the doc table) and an
# unordered set would make the contract test pass on a reordered tuple.
SIGNAL_SPEC = {
    DB_INTEGRITY_OK: {"type": "gauge", "unit": "1 (passed) / 0 (failed)", "labels": ()},
    REQUESTS_TOTAL: {"type": "counter", "unit": "requests", "labels": ("method", "path", "status_class")},
    REQUEST_DURATION_MS: {"type": "histogram", "unit": "milliseconds", "labels": ("method", "path")},
    SESSIONS_ACTIVE: {"type": "gauge", "unit": "sessions", "labels": ()},
    SUBMISSIONS_TOTAL: {"type": "counter", "unit": "submissions", "labels": ("stage",)},
    DB_LOCK_ERRORS_TOTAL: {"type": "counter", "unit": "errors", "labels": ()},
    DB_WRITE_DURATION_MS: {"type": "histogram", "unit": "milliseconds", "labels": ("operation",)},
    DB_SIZE_BYTES: {"type": "gauge", "unit": "bytes", "labels": ("file",)},
    VOLUME_FREE_BYTES: {"type": "gauge", "unit": "bytes", "labels": ()},
    DEPENDENCY_UP: {"type": "gauge", "unit": "1 (up) / 0 (down)", "labels": ("dependency",)},
    DEPENDENCY_PROBE_DURATION_MS: {"type": "gauge", "unit": "milliseconds", "labels": ("dependency",)},
    READYZ_OK: {"type": "gauge", "unit": "1 (ready) / 0 (not ready)", "labels": ()},
    READYZ_LAST_CHECK_UNIXTIME: {"type": "gauge", "unit": "unix seconds", "labels": ()},
    TELEMETRY_LAST_SUCCESS_UNIXTIME: {"type": "gauge", "unit": "unix seconds", "labels": ()},
    PROCESS_START_UNIXTIME: {"type": "gauge", "unit": "unix seconds", "labels": ()},
    LAST_SUBMISSION_UNIXTIME: {"type": "gauge", "unit": "unix seconds", "labels": ()},
    LAST_SESSION_MINT_UNIXTIME: {"type": "gauge", "unit": "unix seconds", "labels": ()},
}

# Prometheus-compatible bucket edges. Request duration: a healthy /case answers in
# single-digit ms on this stack; a 5 s bucket edge exists because busy_timeout is
# 5 s, so a sample landing there is a lock wait, not "a slow request".
REQUEST_DURATION_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)
DB_WRITE_BUCKETS_MS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 5000)

# Bounded label set for `path`. Arbitrary client paths (a 404, a scanner
# probing /wp-admin) would otherwise create unbounded cardinality in both the
# snapshot and any future metric store -- the classic unbounded-label bug. One
# fixed list, unmatched -> "<other>": the aggregate is what an alert needs.
KNOWN_PATHS = frozenset(
    {
        "/healthz",
        "/livez",
        "/readyz",
        "/internal/signals",
        "/session",
        "/session/revoke",
        "/session/end",
        "/case",
        "/submit",
        "/reset",
        "/review",
        "/studies",
        "/view",
        "/finalize",
        "/results",
        "/orthanc-authz",
    }
)

PROCESS_START_UNIXTIME_VALUE = time.time()

_lock = threading.Lock()
_counters: dict[tuple, float] = defaultdict(float)
_gauges: dict[tuple, float] = {}
_histograms: dict[tuple, dict] = {}

LabelKey = tuple[str, tuple[tuple[str, str], ...]]


def _key(name: str, labels: dict | None) -> LabelKey:
    return name, tuple(sorted((labels or {}).items()))


def path_label(path: str) -> str:
    """Bounded `path` label -- see KNOWN_PATHS."""
    return path if path in KNOWN_PATHS else "<other>"


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


def inc_counter(name: str, labels: dict | None = None, amount: float = 1) -> None:
    with _lock:
        _counters[_key(name, labels)] += amount


def set_gauge(name: str, value: float, labels: dict | None = None) -> None:
    with _lock:
        _gauges[_key(name, labels)] = value


def get_gauge(name: str, labels: dict | None = None) -> float | None:
    with _lock:
        return _gauges.get(_key(name, labels))


def counter_value(name: str, labels: dict | None = None) -> float:
    with _lock:
        return _counters.get(_key(name, labels), 0.0)


def observe_histogram(name: str, value: float, buckets: tuple, labels: dict | None = None) -> None:
    """Increment EVERY bucket whose edge the value falls under (cumulative, as
    Prometheus histograms are defined) instead of one bucket and summing at
    render time -- one representation, no chance of the two disagreeing."""
    key = _key(name, labels)
    with _lock:
        entry = _histograms.get(key)
        if entry is None:
            entry = _histograms[key] = {
                "edges": tuple(buckets),
                "bucket_counts": [0] * len(buckets),
                "count": 0,
                "sum": 0.0,
            }
        entry["count"] += 1
        entry["sum"] += value
        for index, edge in enumerate(buckets):
            if value <= edge:
                entry["bucket_counts"][index] += 1


def record_request(method: str, path: str, status_code: int, duration_ms: float) -> None:
    label = path_label(path)
    inc_counter(REQUESTS_TOTAL, {"method": method, "path": label, "status_class": status_class(status_code)})
    observe_histogram(REQUEST_DURATION_MS, duration_ms, REQUEST_DURATION_BUCKETS_MS, {"method": method, "path": label})


def record_submission(stage: str) -> None:
    inc_counter(SUBMISSIONS_TOTAL, {"stage": stage})


def observe_db_write(operation: str, duration_ms: float) -> None:
    observe_histogram(DB_WRITE_DURATION_MS, duration_ms, DB_WRITE_BUCKETS_MS, {"operation": operation})


def is_db_lock_error(exc: BaseException) -> bool:
    """A SQLite writer-lock timeout (`database is locked` / `database table is
    locked`) after busy_timeout ran out. Distinct from every other
    OperationalError because it is transient and retryable -- #222 will map it
    to 503 + Retry-After; until then it is still counted here so the signal
    exists ahead of that change (issue #113's own acceptance criterion)."""
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def record_db_lock_error() -> None:
    inc_counter(DB_LOCK_ERRORS_TOTAL)
    logger.warning("db_lock_timeout")


def reset() -> None:
    """Tests only: counters are process-lifetime state."""
    with _lock:
        _counters.clear()
        _gauges.clear()
        _histograms.clear()


# ---- Rendered snapshot ----


def _counter_samples() -> list[dict]:
    with _lock:
        items = list(_counters.items())
    return [
        {"name": name, "labels": dict(labels), "value": value}
        for (name, labels), value in sorted(items, key=lambda item: (item[0][0], item[0][1]))
    ]


def _gauge_samples() -> list[dict]:
    with _lock:
        items = list(_gauges.items())
    return [
        {"name": name, "labels": dict(labels), "value": value}
        for (name, labels), value in sorted(items, key=lambda item: (item[0][0], item[0][1]))
    ]


def _histogram_samples() -> list[dict]:
    with _lock:
        items = list(_histograms.items())
    samples = []
    for (name, labels), entry in sorted(items, key=lambda item: (item[0][0], item[0][1])):
        # Edges are stored with the entry, not looked up by name: a histogram
        # is defined by its own bucket layout, and a name->edges map would
        # silently KeyError the day a third one is added.
        edges = entry["edges"]
        samples.append(
            {
                "name": name,
                "labels": dict(labels),
                "buckets": [{"le": edge, "count": entry["bucket_counts"][i]} for i, edge in enumerate(edges)],
                "count": entry["count"],
                "sum": round(entry["sum"], 3),
            }
        )
    return samples


def _db_gauge(conn, query: str, params: tuple = ()):
    """One DB-derived gauge: returns None instead of raising, so a broken
    database degrades the snapshot (the caller sees db_available=false) rather
    than killing the very endpoint an operator uses to diagnose it."""
    try:
        row = conn.execute(query, params).fetchone()
    except sqlite3.Error:
        logger.exception("telemetry_db_gauge_failed", extra={"query": query})
        return None
    return None if row is None else row[0]


def _file_size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def _free_bytes(directory: str) -> int | None:
    try:
        stats = os.statvfs(directory)
    except OSError:
        return None
    return stats.f_bavail * stats.f_frsize


def _summary(samples: dict, now: float) -> dict:
    """The flat, fixed-shape subset that goes into the periodic
    `telemetry_snapshot` log line: log-driver-based alerting needs stable
    field names, not a nested document that changes shape as signals are
    added."""

    def gauge(name: str):
        for sample in samples["gauges"]:
            if sample["name"] == name and not sample["labels"]:
                return sample["value"]
        return None

    def counter_sum(name: str, match: dict | None = None) -> float:
        return sum(
            sample["value"]
            for sample in samples["counters"]
            if sample["name"] == name and all(sample["labels"].get(k) == v for k, v in (match or {}).items())
        )

    last_submission = gauge(LAST_SUBMISSION_UNIXTIME)
    return {
        "sessions_active": gauge(SESSIONS_ACTIVE),
        "requests_total": counter_sum(REQUESTS_TOTAL),
        "requests_4xx_total": counter_sum(REQUESTS_TOTAL, {"status_class": "4xx"}),
        "requests_5xx_total": counter_sum(REQUESTS_TOTAL, {"status_class": "5xx"}),
        "submissions_total": counter_sum(SUBMISSIONS_TOTAL),
        "db_lock_errors_total": counter_sum(DB_LOCK_ERRORS_TOTAL),
        "db_size_bytes": gauge(DB_SIZE_BYTES),
        "volume_free_bytes": gauge(VOLUME_FREE_BYTES),
        "dependency_orthanc_up": gauge(DEPENDENCY_UP),
        "readyz_ok": gauge(READYZ_OK),
        "last_submission_age_seconds": (None if last_submission is None else round(now - last_submission, 1)),
        "telemetry_last_success_unixtime": gauge(TELEMETRY_LAST_SUCCESS_UNIXTIME),
    }


def signals(conn=None, now: float | None = None) -> dict:
    """Build the health/storage/API snapshot served by GET /internal/signals.

    `conn` is optional on purpose: with a dead database the endpoint still
    answers, with `db_available: false` and every DB-derived gauge absent --
    "the probe works and reports a broken database" is exactly the distinction
    DoD 11 asks for, and returning 503 here would hide it.
    """
    now = time.time() if now is None else now
    db_available = conn is not None

    if conn is not None:
        sessions_active = _db_gauge(conn, "SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (now,))
        if sessions_active is not None:
            set_gauge(SESSIONS_ACTIVE, sessions_active)
        last_submission = _db_gauge(conn, "SELECT MAX(submitted_at) FROM submissions")
        if last_submission is not None:
            set_gauge(LAST_SUBMISSION_UNIXTIME, last_submission)
        last_mint = _db_gauge(conn, "SELECT MAX(created_at) FROM sessions")
        if last_mint is not None:
            set_gauge(LAST_SESSION_MINT_UNIXTIME, last_mint)

        from . import db as db_module  # local import: keeps this module importable on its own

        for suffix in ("", "-wal", "-shm"):
            size = _file_size(db_module.DB_PATH + suffix)
            if size is not None:
                set_gauge(DB_SIZE_BYTES, size, {"file": "db" if not suffix else suffix[1:]})
        free = _free_bytes(os.path.dirname(os.path.abspath(db_module.DB_PATH)) or ".")
        if free is not None:
            set_gauge(VOLUME_FREE_BYTES, free)

    set_gauge(PROCESS_START_UNIXTIME, PROCESS_START_UNIXTIME_VALUE)
    set_gauge(TELEMETRY_LAST_SUCCESS_UNIXTIME, now)

    samples = {
        "generated_at": now,
        "process_start_unixtime": PROCESS_START_UNIXTIME_VALUE,
        "db_available": db_available,
        "counters": _counter_samples(),
        "gauges": _gauge_samples(),
        "histograms": _histogram_samples(),
    }
    samples["summary"] = _summary(samples, now)
    return samples


# ---- Periodic snapshot line (stdout JSON, no scrape needed) ----

SNAPSHOT_INTERVAL_ENV = "GRADING_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS"
DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 30


def snapshot_interval_seconds() -> float:
    """0 disables the periodic line (tests, and anyone using the JSON endpoint
    or a log pipeline that would rather poll). Invalid values fall back to the
    default instead of aborting startup: telemetry must never be the reason
    the grading service refuses to run. Fractional values are allowed so the
    thread's own test can run one tick without sleeping a whole second."""
    raw = os.environ.get(SNAPSHOT_INTERVAL_ENV)
    if raw is None or raw.strip() == "":
        return float(DEFAULT_SNAPSHOT_INTERVAL_SECONDS)
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("telemetry_snapshot_interval_invalid", extra={"value": raw[:60]})
        return float(DEFAULT_SNAPSHOT_INTERVAL_SECONDS)


def emit_snapshot(conn=None) -> dict:
    """Build a snapshot and log it as one `telemetry_snapshot` line. Returns the
    snapshot so the caller (or a test) can assert on it without parsing logs."""
    snapshot = signals(conn)
    logger.info("telemetry_snapshot", extra=snapshot["summary"])
    return snapshot


def _snapshot_tick() -> None:
    """One iteration of the periodic line, extracted so a test can exercise it
    without racing a thread. Never raises: telemetry failing is itself a
    signal, not a reason to stop the service."""
    from . import db as db_module

    conn = None
    try:
        conn = db_module.get_connection()
        emit_snapshot(conn)
    except Exception:  # noqa: BLE001 - never let telemetry take the service down
        logger.exception("telemetry_snapshot_failed")
    finally:
        if conn is not None:
            conn.close()


def start_snapshot_thread() -> "callable":
    """Start the daemon thread that emits `telemetry_snapshot` every
    `GRADING_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS` (default 30, 0 disables).
    Returns a stopper; the lifespan's finally: calls it so a TestClient
    context cannot leave a thread behind."""
    interval = snapshot_interval_seconds()
    if interval <= 0:
        return lambda: None

    stopped = threading.Event()

    def _loop():
        while not stopped.wait(interval):
            _snapshot_tick()

    thread = threading.Thread(target=_loop, name="telemetry-snapshot", daemon=True)
    thread.start()

    def _stop():
        stopped.set()
        thread.join(timeout=2)

    return _stop
