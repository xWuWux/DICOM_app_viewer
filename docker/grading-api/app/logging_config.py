"""
Structured (JSON) logging for grading-api (issue #72).

Logs to stdout, one JSON object per line -- container-native, no log-
shipping agent needed (`docker logs` / whatever log driver the real
deployment uses already captures stdout like any other process). This is
the foundation several other things build on: issue #62 (the /healthz
exception-text leak) is fixed by giving the server somewhere real to put
the exception instead of the client response; #74's error taxonomy logs
every error response through here too.

Deliberately never logs: the raw session token, GRADING_COORDINATOR_KEY,
VNC/Guacamole secrets (n/a in this service, but same principle), or
free-text student answers. student_id/session_id are also kept out of
log lines on the same principle -- they're pseudonymous IDs, not
necessarily safe to treat as fully non-identifying in a real deployment.
request_id (below) is what correlates a request's log lines instead.

httpx's own logger is explicitly quieted below: found empirically while
writing this module's own tests -- httpx (used internally by FastAPI's
TestClient, and by nothing else in this service) logs the full request
URL, including query strings, at INFO level. That's test-harness-only
today (this service never makes outbound httpx calls itself), but it's
exactly the kind of thing that silently reintroduces a leak if that ever
changes, so it's silenced at the source rather than relied on to stay
irrelevant.
"""

import contextvars
import json
import logging
import sys
import time

# Set by RequestIdMiddleware (main.py) at the start of every request.
# contextvars (not a plain module global) because request handling is
# async: a plain global would leak between concurrently in-flight requests.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    """Stamps the current request_id onto every LogRecord that reaches
    this handler, regardless of which logger originated it -- this is
    what makes request_id an inspectable attribute on the record itself
    (e.g. for tests using caplog), not just something baked into the
    formatted string."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# Every attribute a bare logging.LogRecord already carries, plus
# request_id (stamped by _RequestIdFilter above, already given its own
# explicit key in the formatter -- would otherwise get processed twice)
# -- used to find which extra=... fields a caller added, so those can be
# surfaced as their own top-level JSON keys automatically (e.g.
# logger.warning("auth_failed", extra={"error_code": "AUTH_INVALID_TOKEN"})
# -> {"error_code": "...", ...} in the emitted line) without every call
# site needing custom formatting.
_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "request_id",
}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Called once, at module import time (see main.py) -- not per-request
    and not inside the FastAPI lifespan, which would re-run on every
    TestClient context in tests and fight pytest's own caplog handler."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # See this module's own docstring for why.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
