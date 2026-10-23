"""Coordinator CLI adapter for the API's shared secret-file reader.

The shared module is stdlib-only and importing it does not import app.config or
initialize a database. Keep one file/normalization contract for server and CLIs.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker" / "grading-api"))
from app.secret_files import SecretFileError, read_secret


def coordinator_key(environ=None, *, required=True):
    env = os.environ if environ is None else environ
    try:
        value = read_secret("GRADING_COORDINATOR_KEY", env)
        if required and not value:
            raise SecretFileError(
                "Set GRADING_COORDINATOR_KEY_FILE or GRADING_COORDINATOR_KEY."
            )
        if "GRADING_COORDINATOR_KEY_FILE" in env and len(value) < 32:
            raise SecretFileError(
                "GRADING_COORDINATOR_KEY_FILE must contain at least 32 characters."
            )
    except SecretFileError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    return value or ""
