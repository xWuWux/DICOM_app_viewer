"""Read mounted secrets without exporting their contents (issue #216)."""

import os
import stat
from collections.abc import Mapping

MAX_SECRET_BYTES = 4096


class SecretFileError(ValueError):
    """Configuration failure containing names only, never values or paths."""


def read_secret(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Retain legacy env behavior; an explicitly selected file must work.

    Accept one optional LF/CRLF from secret-file tooling, not arbitrary strip().
    Restrict files to private, regular, bounded ASCII text. O_NONBLOCK prevents
    an accidentally configured FIFO from hanging startup before fstat().
    """
    env = os.environ if environ is None else environ
    file_name = name + "_FILE"
    if file_name not in env:
        return env.get(name)
    if env.get(name):
        raise SecretFileError(f"Set only {name} or {file_name}, not both.")
    if not env[file_name]:
        raise SecretFileError(f"{file_name} is empty; specify a readable private file.")
    try:
        fd = os.open(env[file_name], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as secret:
            info = os.fstat(secret.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in (0o400, 0o600):
                raise SecretFileError(f"{file_name} must reference a regular file with mode 0400 or 0600.")
            raw = secret.read(MAX_SECRET_BYTES + 1)
    except SecretFileError:
        raise
    except (OSError, ValueError):
        raise SecretFileError(f"Cannot read {file_name}; check the mount and file ownership/permissions.") from None
    if len(raw) > MAX_SECRET_BYTES:
        raise SecretFileError(f"{file_name} exceeds the secret-file size limit.")
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or any(c < 33 or c > 126 for c in raw):
        raise SecretFileError(f"{file_name} must contain non-empty printable ASCII without whitespace.")
    return raw.decode("ascii")
