"""
Stable error taxonomy for grading-api (issue #74). Every error response,
from every endpoint, has the same shape:

    {"error_code": "AUTH_INVALID_TOKEN", "message": "Invalid or unknown token"}

instead of the previous ad hoc HTTPException(code, "free-text string")
per call site. error_code is the fixed, documented set below -- add a new
one here when a new failure case needs distinguishing; never invent one
inline at the raise site.

The two catch-all handlers (validation_error_handler,
unhandled_exception_handler) exist so *every* error path gets the same
shape and gets logged, including ones this file never anticipated -- not
just the ones explicitly raised as AppError. This is also where issue
#62 actually gets fixed: the real exception is logged here, server-side,
in full; the client only ever gets a generic message.
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .logging_config import get_logger

logger = get_logger(__name__)

# issue #93: bounds on what validation_error_handler may log. An
# unbounded exc.errors() dump logged the *entire rejected payload* --
# including a live session token in `input` (POST /submit with a missing
# case_id logs the whole body, token and all) and up to ~20 KB of raw
# student free text per rejected request. Only loc/type/msg are ever
# logged, msg is truncated, and the list itself is capped, so one
# rejected request can never produce an unbounded log line.
_MAX_LOGGED_VALIDATION_ERRORS = 10
_MAX_LOGGED_MSG_CHARS = 200


class AppError(Exception):
    """Raise this instead of fastapi.HTTPException everywhere in this
    service, so every deliberate error response goes through
    app_error_handler and gets the same {"error_code", "message"} shape."""

    def __init__(self, status_code: int, error_code: str, message: str):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "app_error",
        extra={"error_code": exc.error_code, "status_code": exc.status_code, "path": request.url.path},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": exc.error_code, "message": exc.message},
    )


def _sanitized_validation_errors(errors: list) -> list:
    """issue #93: keep the diagnostic part of each pydantic error (which
    field, which failure type, why) and drop everything value-derived.

    `input` is the rejected value itself -- a live session token on a
    partial /submit body, an entire student answer on an overlong `text`
    -- and `ctx` can embed value text too. Neither is needed to diagnose
    *which field* failed, so neither is ever logged."""
    sanitized = []
    for err in errors[:_MAX_LOGGED_VALIDATION_ERRORS]:
        msg = str(err.get("msg", ""))[:_MAX_LOGGED_MSG_CHARS]
        sanitized.append({"loc": list(err.get("loc", [])), "type": err.get("type"), "msg": msg})
    if len(errors) > _MAX_LOGGED_VALIDATION_ERRORS:
        sanitized.append({"truncated_count": len(errors) - _MAX_LOGGED_VALIDATION_ERRORS})
    return sanitized


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Logged server-side for real diagnostic value (which field, what
    # pydantic rejected) but never echoed to the client: it could mean
    # bouncing a rejected payload back at whoever sent it -- an
    # amplification vector, not just noise. And since issue #93, the
    # *logged* form is sanitized too (no input/ctx, capped) -- logging
    # exc.errors() raw meant every 422 wrote, in cleartext, a session
    # token minted moments earlier and/or the student's whole answer.
    logger.info(
        "validation_error",
        extra={"path": request.url.path, "errors": _sanitized_validation_errors(exc.errors())},
    )
    return JSONResponse(
        status_code=422,
        content={"error_code": "VALIDATION_ERROR", "message": "Invalid request"},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # The one place a truly unanticipated exception (a bug, not a
    # deliberate AppError) ends up -- logged with the full traceback
    # (logger.exception, not .error) so it's actually diagnosable, while
    # the client only ever sees a generic message. This is issue #62's
    # actual fix, generalized to every endpoint rather than patched once
    # at /healthz specifically.
    logger.exception("unhandled_exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={"error_code": "SERVER_ERROR", "message": "Internal server error"},
    )
