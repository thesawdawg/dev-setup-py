"""The bearer token for `devstuff mcp --http` (docs/specs/mcp, FR-24, SD-7, SD-8).

It lives in a file, not on a command line or in a URL: a file path can be logged and pasted safely,
and a token cannot. The file persists across restarts, so a client's `Authorization` header stays
right (a fresh token per run broke every configured client on every restart — SD-8). It is held to
an ssh key's standard: owned by the user, private, and **never printed** — not here, not in an
error, not in the banner.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

MIN_LENGTH = 16  # a hand-written token shorter than this is a password, and this one guards an installer


class TokenError(RuntimeError):
    """The token file cannot be used. Messages never contain the token."""


def default_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "devstuff" / "mcp-token"


def load(path: Path | None = None, *, rotate: bool = False) -> tuple[str, Path]:
    """The token and the file it came from, creating or replacing the file as asked.

    A file readable or writable by group or others is refused *even when rotating*: the old secret
    was exposed, and quietly fixing the mode would hide that from the one person who needs to know.
    """
    path = path or default_path()
    try:
        if not path.exists() and not path.is_symlink():
            return _write(path), path
        _check_mode(path)
        if rotate:
            return _write(path), path
        return _read(path), path
    except OSError as exc:
        raise TokenError(f"cannot use token file {path}: {exc.strerror or exc}") from exc


def _check_mode(path: Path) -> None:
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise TokenError(
            f"token file {path} has unsafe permissions ({mode:04o}): it must not be accessible to group "
            f"or others. Fix it with `chmod 600 {path}`, or replace it with --rotate-token after "
            "treating the old token as exposed"
        )


def _read(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < MIN_LENGTH or not value.isascii() or not value.isprintable() or " " in value:
        raise TokenError(
            f"token file {path} does not hold a usable token (at least {MIN_LENGTH} printable characters, "
            "one line, no spaces). Put one there, or let --rotate-token write a new one"
        )
    return value


def _write(path: Path) -> str:
    value = secrets.token_urlsafe(32)
    created = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if created:
        # A directory this module creates is private. One that already exists is left alone:
        # `--token-file` may point into a directory the user keeps for other reasons.
        path.parent.chmod(0o700)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
        os.replace(tmp, path)  # atomic: a reader never sees a half-written token
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return value
