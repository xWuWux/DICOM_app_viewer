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


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # exc.errors() is logged in full server-side (real diagnostic value --
    # which field, what pydantic rejected) but never echoed to the client:
    # it can include the actual rejected input value, which for a
    # max_length violation could mean bouncing a large payload straight
    # back at whoever sent it -- an amplification vector, not just noise.
    logger.info("validation_error", extra={"path": request.url.path, "errors": exc.errors()})
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
