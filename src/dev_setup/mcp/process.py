"""FR-11a: give the server no controlling terminal (docs/specs/mcp, F-11, SD-12).

A `/dev/null` stdin is not "no terminal": `sudo` opens `/dev/tty` directly and prompted on the
user's terminal even with fd 0 closed off (F-11). An installer waiting on a password nobody can
type would hang the client's request, so the server detaches at the *process* level, before the
fd move and before any tool can run.

`os.setsid()` does it when a client spawned the server. A shell starting `devstuff mcp --http`
makes the process a group leader, where `setsid` fails with `EPERM`; then the process forks, the
child calls `setsid` and is the server, and the original stays behind as a thin supervisor that
forwards signals and mirrors the exit status — so Ctrl-C and a closed terminal still stop it even
though it no longer owns the terminal.

The alternative, `start_new_session=True` on each of the six subprocess call sites, is the
per-call-site audit FR-11 already refuses to depend on: the next `subprocess` anyone writes would
silently reopen it.
"""

from __future__ import annotations

import errno
import os
import signal
from collections.abc import Callable


class TerminalReachable(RuntimeError):
    """The server could not give up its controlling terminal and may run an installer."""


def detach(
    *,
    setsid: Callable[[], object] = os.setsid,
    fork: Callable[[], int] = os.fork,
    supervise: Callable[[int], object] | None = None,
) -> str:
    """Leave the controlling terminal. Returns how: `"setsid"`, `"forked"` (this is the server
    child), or — only when a test injects `supervise` — `"supervisor"`. The real supervisor never
    returns; it exits with the server's status."""
    try:
        setsid()
        return "setsid"
    except OSError as exc:
        if exc.errno != errno.EPERM:
            raise  # not the group-leader case: do not paper over an unexpected failure
    pid = fork()
    if pid == 0:
        setsid()  # the child is not a group leader, so this one cannot fail with EPERM
        return "forked"
    (supervise or _supervise)(pid)
    return "supervisor"


def _supervise(child: int) -> None:
    """Forward signals to the server and exit with its status. Never returns."""
    # This process keeps no copy of the client's stdio: the server child owns the channel, and a
    # supervisor holding the pipe open would keep a peer from seeing EOF when the server exits.
    for fd in (0, 1):
        try:
            os.close(fd)
        except OSError:
            pass

    def forward(signum: int, _frame: object) -> None:
        # SIGHUP is what a closing terminal sends. The server is in its own session and would never
        # see it, so it would be orphaned and keep running: it is turned into the polite stop.
        target = signal.SIGTERM if signum == signal.SIGHUP else signum
        try:
            os.kill(child, target)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, forward)

    while True:
        try:
            _, status = os.waitpid(child, 0)
            break
        except InterruptedError:
            continue
        except ChildProcessError:
            os._exit(1)
    code = os.waitstatus_to_exitcode(status)
    # A signalled child reads as 128+N, the shell's convention, so a caller can tell it from a status.
    os._exit(code if code >= 0 else 128 - code)


def terminal_reachable(*, open_: Callable[..., int] = os.open) -> bool:
    """Can this process open `/dev/tty`? `O_NOCTTY` so asking never acquires one as a side effect."""
    try:
        fd = open_("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False  # ENXIO (no controlling terminal), ENOENT (a container) — either way, none
    try:
        os.close(fd)
    except OSError:
        pass
    return True


def require_no_terminal(
    *,
    gated: bool,
    warn: Callable[[str], None],
    reachable: Callable[[], bool] | None = None,
) -> None:
    """Verify the detach worked, and fail closed (FR-11a.3).

    With a gate flag on, the server can run an installer, so a reachable terminal is a refusal to
    start — nothing that can prompt for a password starts while one is reachable. Read-only, there
    is nothing that prompts, so it warns and continues."""
    if not (reachable or terminal_reachable)():
        return
    if gated:
        raise TerminalReachable(
            "could not detach from the controlling terminal, and --allow-install/--allow-functions "
            "can run an installer that would prompt on it; refusing to start"
        )
    warn("still attached to a controlling terminal (read-only tools never prompt, so continuing)")
