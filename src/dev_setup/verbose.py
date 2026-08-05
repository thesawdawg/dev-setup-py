"""Process-wide verbosity level and the logging helpers built on it.

Three levels, set once per process by the `-v` CLI option (`verbose.option`) or the
`DEVSTUFF_VERBOSE` environment variable:

    0  quiet   — normal output only; subprocess output is captured and shown only on failure
    1  -v      — every command devstuff runs is logged, and its output streams live
    2  -vv     — plus read-only probes, exit codes, captured output, and `bash -x` tracing
                 of script bodies (functions, install/remove scripts)

**Everything here writes to stderr, never stdout.** `devstuff run <key>` in
`register: eval` mode prints shell code to stdout for `eval "$(...)"` to consume, so a
verbose line on stdout would be executed in the user's shell. Routing the whole logger
to stderr makes that impossible by construction rather than by remembering to check the
level at each call site.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Generator
from contextlib import contextmanager

from rich.console import Console

# stderr, and no highlighting/markup: these lines carry raw command lines and script
# bodies, which are full of characters rich would otherwise style or eat.
_console = Console(stderr=True, highlight=False)

# soft_wrap keeps a logged command line or script line on one terminal line. Rich's
# default word-wrap breaks a long command mid-word, and what the user most wants to do
# with a verbose line — copy it back into a shell — stops working when it does.
_PRINT = {"style": "dim", "markup": False, "soft_wrap": True}

QUIET = 0
VERBOSE = 1
TRACE = 2

_level: int = 0


def _from_env() -> int:
    """DEVSTUFF_VERBOSE=1|2 (or a bare word like "true") as a starting level.

    Lets a function invoked from a script or another tool be verbose without the caller
    threading a flag through; an explicit -v still wins because it can only raise it.
    """
    raw = os.environ.get("DEVSTUFF_VERBOSE", "").strip().lower()
    if not raw:
        return QUIET
    if raw.isdigit():
        return min(int(raw), TRACE)
    return VERBOSE if raw in ("true", "yes", "on", "y") else QUIET


_level = _from_env()


def set_level(value: int) -> None:
    global _level
    _level = max(QUIET, min(TRACE, value))


def level() -> int:
    return _level


def enabled(minimum: int = VERBOSE) -> bool:
    return _level >= minimum


def log(msg: str, *, minimum: int = VERBOSE) -> None:
    """A verbose note. Dim, stderr, one line."""
    if _level >= minimum:
        _console.print(f"  {msg}", **_PRINT)


def trace(msg: str) -> None:
    """A note only worth showing at -vv."""
    log(msg, minimum=TRACE)


def command(cmd: list[str] | str, *, cwd: object = None, minimum: int = VERBOSE) -> None:
    """Log a command about to run, in a form the user could paste back into a shell."""
    if _level < minimum:
        return
    text = cmd if isinstance(cmd, str) else shlex.join(str(c) for c in cmd)
    log(f"$ {text}" + (f"   (in {cwd})" if cwd else ""), minimum=minimum)


def result(returncode: int, output: str = "", *, minimum: int = TRACE) -> None:
    """Log a captured command's exit code and output."""
    if _level < minimum:
        return
    log(f"→ exit {returncode}", minimum=minimum)
    block(output, minimum=minimum)


def block(text: str, *, minimum: int = TRACE) -> None:
    """Log a multi-line body (captured output, a script) indented under the last line."""
    if _level < minimum or not text.strip():
        return
    for line in text.rstrip("\n").splitlines():
        _console.print(f"    │ {line}", **_PRINT)


@contextmanager
def step(label: str) -> Generator[None, None, None]:
    """A unit of work: a spinner normally, a logged line when verbose.

    The spinner has to go when verbose — it repaints its own line, so it would fight
    with the subprocess output now streaming to the same terminal.
    """
    if enabled():
        log(label)
        yield
    else:
        from dev_setup import ui
        with ui.spinner(label):
            yield


def option(f):
    """`-v`/`-vv` for a Click command or group, setting the process-wide level.

    `expose_value=False` — the level is process state read by the subprocess helpers
    deep in `generic.py`/`function_runner.py`, not something each command body threads
    through. Attached to both the `cli` group and individual commands so that
    `devstuff -v install x` and `devstuff install -v x` both work; the callback takes
    the max so the two can't cancel each other out.
    """
    import click

    def _callback(ctx: object, param: object, value: int) -> int:
        if value:
            set_level(max(level(), value))
        return value

    return click.option(
        "-v",
        "--verbose",
        count=True,
        expose_value=False,
        is_eager=True,
        callback=_callback,
        help="Verbose output: -v logs and streams every command, -vv adds probes and tracing.",
    )(f)
