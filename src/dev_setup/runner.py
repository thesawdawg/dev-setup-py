"""Supervised child lifecycle for state-changing commands (install/remove/update).

One runner serves every render mode — live view, -v/-vv streaming, explicit plain,
and non-tty/JSON — so the timeout, EOF and cleanup policy is chosen by the command,
never by how the output happens to be shown. Design contract:
docs/specs/install-reliability-terminal-ux/ (R1–R5).

Managed mode (the default): the child runs in its own session (start_new_session)
with stdin disconnected and stdout+stderr merged onto one pipe. The pipe is read
non-blockingly with an incremental UTF-8 decoder; after the primary process exits
the pipe gets a fixed drain window and remaining writers — detached grandchildren,
a deliberately backgrounded service — are dropped rather than waited on. Nothing
is ever inferred from the child's output: the result is the exit code.

Terminal passthrough is the explicit opt-out for trusted scripts that genuinely
need input: it requires `passthrough=True` *and* `DEVSTUFF_TERMINAL_INPUT=1` in the
environment *and* real ttys on stdin+stderr *and* not machine mode. The child then
inherits stdin and writes to the terminal's stderr fd directly, in our own session —
sudo can prompt, keys work — but its output bypasses the pipe, so the action log
records metadata only and the caller is warned.

`machine_mode()` marks contexts (e.g. `--json`) where no prompt or terminal
ownership is ever allowed; `action_log()` binds a private per-run log file that
run() appends output and exit metadata to (never argv or environment).
"""

from __future__ import annotations

import codecs
import contextvars
import os
import select
import signal
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console

# stderr-only diagnostics console: like verbose.py, nothing here may reach stdout —
# `run` in eval mode and `--json` put data there.
_err = Console(stderr=True, highlight=False)


def _warn(message: str) -> None:
    _err.print(f"  {message}", markup=False)


# ── tunables ───────────────────────────────────────────────────────────────────
# Pipe poll cadence; post-exit drain window; grace between escalation steps.
# The drain window is fixed, not idle-reset: a daemon that keeps writing must not
# hold the run open, and one that went quiet is over anyway.
_PIPE_POLL_S = 0.2
_DRAIN_S = 2.0
_TERMINATE_GRACE_S = 2.0
# Cap on the failure-message tail and on the per-line buffer a consumer builds.
_TAIL_CHARS = 8192
# Per-run log: 1 MiB of output, 20 files retained.
_LOG_MAX_BYTES = 1 << 20
_LOG_KEEP = 20
_PASSTHROUGH_ENV = "DEVSTUFF_TERMINAL_INPUT"


class Failed(RuntimeError):
    """The child exited non-zero. `tail` is the bounded last-chars of its output —
    the message is the exit code, never something parsed out of the output.
    `message` lets an adapter re-raise with its own summary while keeping the
    type, rc and tail intact for upstream `except Failed` handlers."""

    def __init__(self, rc: int, tail: str = "", *, message: str | None = None):
        super().__init__(message or tail.strip() or f"exit code {rc}")
        self.rc = rc
        self.tail = tail


class TimedOut(RuntimeError):
    """The explicit caller deadline passed and the child had to be terminated.

    Distinct from `Failed`: a child that exits on its own near the deadline is a
    normal result, but a timed-out-and-killed one must never read as a
    completed operation — apt refresh propagates this rather than retrying."""


# ── machine mode ───────────────────────────────────────────────────────────────

_machine_mode: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "devstuff_machine_mode", default=False
)


@contextmanager
def machine_mode() -> Generator[None, None, None]:
    """Mark a block as machine-output mode: no prompts, no live rendering, no
    terminal passthrough — regardless of what stdin/stderr are attached to."""
    token = _machine_mode.set(True)
    try:
        yield
    finally:
        _machine_mode.reset(token)


def is_machine_mode() -> bool:
    return _machine_mode.get()


# ── per-action logs ────────────────────────────────────────────────────────────

_action_log: contextvars.ContextVar[_ActionLog | None] = contextvars.ContextVar(
    "devstuff_action_log", default=None
)
_suppress_log_output: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "devstuff_suppress_log_output", default=False
)
_last_log_path: Path | None = None


def runs_dir() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "devstuff" / "runs"


def last_log_path() -> Path | None:
    """The most recent action log this process opened (or attempted)."""
    log = _action_log.get()
    return log.path if log is not None else _last_log_path


def note(message: str) -> None:
    """Record a status line (phase name, outcome) into the bound action log, if
    any. Metadata only — callers must not pass argv or raw error text."""
    log = _action_log.get()
    if log is not None:
        log.meta(message)


def _open_runs_dir(path: Path) -> int | None:
    """A dir_fd for the runs dir, or None if it isn't a private owned real dir.

    The fd is what later opens are anchored to — O_DIRECTORY|O_NOFOLLOW refuses a
    symlink, and fstat then verifies owner and mode on the very object opened.
    Best-effort: anything wrong → no log, never an install failure."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        return None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        # No chmod: a pre-existing directory with loose permissions stays as the
        # owner left it — we just don't write secrets into it.
        os.close(fd)
        return None
    return fd


def _prune_runs_dir(path: Path) -> None:
    """Drop oldest run-*.log files beyond retention — only regular files we own;
    symlinks and anything else stay exactly as they are."""
    try:
        candidates = []
        for p in path.iterdir():
            if not (p.name.startswith("run-") and p.suffix == ".log"):
                continue
            try:
                st = p.lstat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid():
                candidates.append((st.st_mtime, p))
        candidates.sort()
        for _, old in candidates[: max(0, len(candidates) - _LOG_KEEP + 1)]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


_TRUNCATED_MARKER = "# [output truncated — log is capped]\n"


class _ActionLog:
    """One private file per action. Writes are bounded and can never raise."""

    def __init__(self, path: Path, file):
        self.path = path
        self._f = file
        self._bytes = 0
        self._truncated = False

    @classmethod
    def open(cls, tool_key: str, operation: str) -> _ActionLog | None:
        directory = runs_dir()
        dfd = _open_runs_dir(directory)
        if dfd is None:
            return None
        try:
            _prune_runs_dir(directory)
            for _ in range(3):
                name = f"run-{uuid.uuid4().hex}.log"
                try:
                    # Anchored to the verified dir fd — no path is re-resolved.
                    fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=dfd,
                    )
                except FileExistsError:
                    continue
                except OSError:
                    return None
                f = os.fdopen(fd, "w", encoding="utf-8", errors="replace")
                log = cls(directory / name, f)
                log.meta(f"action={operation} tool={tool_key}")
                return log
            return None
        finally:
            os.close(dfd)

    def meta(self, line: str) -> None:
        """A timestamped execution-metadata line — exits, signals, notes. Never
        argv. Once the byte cap is reached these are dropped with the output —
        the truncation marker is the last line the file ever gets."""
        self._write(f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")

    def write(self, text: str) -> None:
        self._write(text)

    def _write(self, text: str) -> None:
        if self._f is None or self._truncated:
            return
        try:
            # The cap counts encoded bytes, not characters — a line of astral
            # characters is ~4 bytes each. Marker space is reserved up front so
            # file contents never exceed _LOG_MAX_BYTES even after truncation.
            marker = _TRUNCATED_MARKER.encode("utf-8")
            budget = _LOG_MAX_BYTES - len(marker) - self._bytes
            data = text.encode("utf-8", "replace")
            if len(data) > budget:
                # errors="ignore" drops a UTF-8 sequence split by the slice —
                # decoding with "replace" would swap it for U+FFFD (3 bytes)
                # and overshoot the cap we're trying to hold.
                kept = data[:budget].decode("utf-8", "ignore").encode("utf-8")
                self._truncated = True
                self._f.write(kept.decode("utf-8") + _TRUNCATED_MARKER)
                self._bytes += len(kept) + len(marker)
            else:
                self._f.write(text)
                self._bytes += len(data)
            self._f.flush()
        except OSError:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None


@contextmanager
def action_log(tool_key: str, operation: str) -> Generator[_ActionLog | None, None, None]:
    """Bind a private per-action log under `<state>/devstuff/runs/` for the block.

    Files are `run-<uuid>.log` (0600, O_NOFOLLOW, exclusive create — no user-chosen
    path is ever followed), the directory is required to be owned by us with no
    group/other access, output is capped and old files pruned. Best-effort
    throughout: failure to log yields None and changes nothing else.
    """
    global _last_log_path
    log = _ActionLog.open(tool_key, operation)
    # A failed open must not leave a previous action's path displayed as if it
    # were this one's log.
    _last_log_path = log.path if log is not None else None
    token = _action_log.set(log)
    try:
        yield log
    except BaseException:
        # The action ended early — record that fact without the raw error,
        # which may carry argv or output text.
        if log is not None:
            log.meta("action ended abnormally")
        raise
    finally:
        _action_log.reset(token)
        if log is not None:
            log.close()


@contextmanager
def suppress_log_output() -> Generator[None, None, None]:
    """Within the block, run() still emits output to the caller but persists only
    metadata to the action log — for -vv `bash -x` runs whose xtrace echo can
    carry expanded secrets."""
    token = _suppress_log_output.set(True)
    try:
        yield
    finally:
        _suppress_log_output.reset(token)


# ── passthrough ────────────────────────────────────────────────────────────────


def _isatty(file) -> bool:
    try:
        return file.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def passthrough_allowed() -> bool:
    """Whether a terminal-owned child is legal *right now*: the operator opted in
    with DEVSTUFF_TERMINAL_INPUT=1, stdin+stderr are real ttys, and no machine
    mode is active. This is the single place that decides — callers that shape
    argv differently for managed vs passthrough children read the same answer."""
    return (
        os.environ.get(_PASSTHROUGH_ENV) == "1"
        and not is_machine_mode()
        and _isatty(sys.stdin)
        and _isatty(sys.stderr)
    )


# ── termination ────────────────────────────────────────────────────────────────


def _wait_exit(proc: subprocess.Popen, grace: float) -> bool:
    end = time.monotonic() + grace
    while time.monotonic() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return proc.poll() is not None


def _signal(sig: int, *, pgid: int | None = None, pid: int | None = None) -> None:
    try:
        if pgid is not None:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _terminate(proc: subprocess.Popen, *, privileged: bool, group: bool) -> bool:
    """Signal the owned workload to stop, bounded. Returns True if the primary
    may still be running afterwards (the caller reports, never claims, that).

    `group` targets the child's own process group (start_new_session made it
    leader). Only invoked while the primary is live — after it exits, leftover
    writers are detached services we must not sweep. Escalation: INT then TERM
    for everything; SIGKILL only for unprivileged children — never SIGKILL a
    privileged package transaction or a daemon. For passthrough children we
    share the group, so signals go to the pid alone.
    """
    target = {"pgid": proc.pid} if group else {"pid": proc.pid}
    if proc.poll() is None:
        _signal(signal.SIGINT, **target)
    if proc.poll() is None and not _wait_exit(proc, _TERMINATE_GRACE_S):
        _signal(signal.SIGTERM, **target)
    if _wait_exit(proc, _TERMINATE_GRACE_S):
        return False
    if privileged:
        _warn(
            "the privileged process did not stop when signalled — it may still be "
            "running; check `ps` and system state before retrying."
        )
        return proc.poll() is None
    _signal(signal.SIGKILL, **target)
    _wait_exit(proc, _TERMINATE_GRACE_S)
    return proc.poll() is None


# ── the runner ─────────────────────────────────────────────────────────────────


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    on_output: Callable[[str], None] | None = None,
    on_draining: Callable[[], None] | None = None,
    timeout: float | None = None,
    privileged: bool = False,
    passthrough: bool = False,
) -> None:
    """Run one state-changing child to completion. Raises Failed (RuntimeError) on
    a non-zero exit, TimedOut on an enforced deadline, RuntimeError on spawn
    failure; KeyboardInterrupt is re-raised after cleanup.

    `on_output` receives decoded text chunks (arbitrary boundaries — no newline
    guarantee, may contain \\r-overwritten progress). `on_draining` fires once
    when the primary process has exited but the output pipe is still open —
    the UI can then say "Finishing output" instead of looking hung. `timeout`
    bounds the whole run from spawn — there is no implicit deadline; callers
    pass one only where a bound is already defined (the 180s apt refresh).
    `privileged` marks a child that may run a root transaction: it gets
    INT/TERM but never SIGKILL, and an unstoppable one is reported rather
    than claimed cleaned.
    """
    argv = [str(c) for c in cmd]
    log = _action_log.get()
    if passthrough and passthrough_allowed():
        _run_passthrough(argv, cwd=cwd, env=env, log=log, timeout=timeout,
                         privileged=privileged)
        return

    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL,   # no hidden `read` — it fails fast on EOF
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merged: one stream, real order
            start_new_session=True,     # own session+pgid: no controlling tty,
        )                               # signals stay scoped to this workload
    except OSError as e:
        if log is not None:
            log.meta("child failed to start")
        raise RuntimeError(str(e)) from e

    tail = ""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def emit(text: str) -> None:
        nonlocal tail
        tail = (tail + text)[-_TAIL_CHARS:]
        if on_output is not None:
            on_output(text)
        if log is not None and not _suppress_log_output.get():
            log.write(text)

    terminated = False

    def terminate() -> bool:
        """Signal the owned workload once. Returns True if it may still run."""
        nonlocal terminated
        if terminated:
            return proc.poll() is None
        terminated = True
        return _terminate(proc, privileged=privileged, group=True)

    try:
        fd = proc.stdout.fileno()
        os.set_blocking(fd, False)
        eof = False
        drain_until: float | None = None
        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline and proc.poll() is None:
                # Only a still-running child "times out": one that exited on
                # its own at the boundary takes the normal exit path below.
                still = terminate()
                msg = f"timed out after {timeout:g}s"
                if still:
                    msg += " — the process may still be running"
                if log is not None:
                    log.meta(msg)
                raise TimedOut(msg)
            if proc.poll() is not None:
                if drain_until is None:
                    drain_until = now + _DRAIN_S
                    if not eof and on_draining is not None:
                        on_draining()
                elif now >= drain_until:
                    break
            if eof:
                # Every writer closed the pipe but the primary still runs —
                # keep polling until it exits (or the deadline), nothing to read.
                if proc.poll() is not None:
                    break
                time.sleep(_PIPE_POLL_S)
                continue
            wait = _PIPE_POLL_S
            # Once the primary is gone the deadline no longer applies — a
            # passed deadline would compute wait=0 and busy-spin the drain.
            for limit in (deadline if drain_until is None else None, drain_until):
                if limit is not None:
                    wait = min(wait, max(0.0, limit - now))
            if not select.select([fd], [], [], wait)[0]:
                continue
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                chunk = b""
            if not chunk:
                eof = True
            else:
                emit(decoder.decode(chunk))
        rest = decoder.decode(b"", final=True)
        if rest:
            emit(rest)
        rc = proc.wait()
    except KeyboardInterrupt:
        terminate()
        if log is not None:
            log.meta("cancelled by user")
        _warn("interrupted — the action may have left partial changes.")
        raise
    except BaseException:
        # A render/log/callback failure must not leak a detached child.
        terminate()
        raise
    finally:
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass
    if log is not None:
        log.meta(f"exit {rc}")
    if rc != 0:
        raise Failed(rc, tail)


def _run_passthrough(
    argv: list[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    log: _ActionLog | None,
    timeout: float | None,
    privileged: bool,
) -> None:
    """Terminal-owned child: inherits stdin, writes to our stderr fd for both
    streams, shares our session — a sudo inside it can prompt and be answered,
    and the tty ticket actually applies. The price: no pipe, so nothing is
    captured to the action log beyond metadata."""
    _warn(
        "terminal passthrough active — child I/O goes to the terminal; "
        "the action log records metadata only."
    )
    try:
        fd_err = sys.stderr.fileno()
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=None,          # inherit — the point of passthrough
            stdout=fd_err,
            stderr=fd_err,
        )
    except OSError as e:
        raise RuntimeError(str(e)) from e
    deadline = None if timeout is None else time.monotonic() + timeout
    terminated = False
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                terminated = True
                still = _terminate(proc, privileged=privileged, group=False)
                msg = f"timed out after {timeout:g}s"
                if still:
                    msg += " — the process may still be running"
                if log is not None:
                    log.meta(msg)
                raise TimedOut(msg)
            time.sleep(_PIPE_POLL_S)
    except KeyboardInterrupt:
        # The tty already signalled the foreground group (us *and* the child) —
        # don't killpg our own group; escalate pid-scoped only.
        if (
            proc.poll() is None
            and not _wait_exit(proc, _TERMINATE_GRACE_S)
            and not terminated
        ):
            _terminate(proc, privileged=privileged, group=False)
        if log is not None:
            log.meta("cancelled by user")
        _warn("interrupted — the action may have left partial changes.")
        raise
    if log is not None:
        log.meta(f"exit {rc} (terminal-owned I/O was not captured)")
    if rc != 0:
        raise Failed(rc, "")
