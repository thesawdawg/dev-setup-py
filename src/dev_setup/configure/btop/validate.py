"""Check a candidate `btop.conf` — offline first, then against the real binary.

The offline half (`verify`) runs before every save and covers what btop will not
say: the splice round-trips, types and ranges hold (btop resets and logs),
enums are in their choice sets and the theme exists (btop *keeps* those silently
and falls back without a word), and `shown_boxes` names real boxes.

The live half (`launch`) is the check btop itself never gives you: write the
candidate into a throwaway HOME, start the real binary under a pty, quit, and
read back the file it saved. A rejected value comes back as the default — the
value diff is the signal. btop.log WARNINGs alone are *not* evidence: the GPU
build logs "Intel GPU: Failed to initialize PMU" on every start on some hosts.

It cannot prove everything, and the check's detail says so: a bad `proc_sorting`
or `color_theme` is kept verbatim by btop — no rejection — so the enum and theme
checks in `verify` remain load-bearing even when launch passes.
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import tempfile
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape

from dev_setup.configure.btop import detect, render
from dev_setup.configure.btop.model import (
    BOXES,
    CONFIG_FILE,
    GPU_BOXES,
    SETTINGS,
    BtopConfig,
)

LAUNCH_WAIT = 2.5
QUIT_TIMEOUT = 3.0
# A real first paint of the TUI is tens of kilobytes; a start failure is a few
# lines. This is how "did it start" is told apart from "it printed an error".
_STARTED_BYTES = 512


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Report:
    version: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]


@dataclass(frozen=True)
class Rejection:
    """A value the candidate asked for that the running btop did not keep."""

    key: str
    wrote: str  # the raw value text the candidate carried
    kept: str  # what the file says after btop saved — empty when the key vanished
    log: tuple[str, ...] = ()  # btop.log lines mentioning the key


@dataclass(frozen=True)
class PtyRun:
    exit_code: int
    output: bytes
    sent_quit: bool  # the process was still alive when 'q' went to it


@dataclass(frozen=True)
class LiveResult:
    started: bool
    exit_code: int
    output: str  # ANSI-stripped pty tail
    rejections: list[Rejection] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The pty runner — shared by launch() and detect's 1.3.0 first-run probe
# ---------------------------------------------------------------------------


def run_pty(binary: str, home: Path, wait: float = LAUNCH_WAIT) -> PtyRun | None:
    """Run `binary` full-screen for `wait` seconds, quit with 'q', reap.

    The child gets a *minimal* environment — HOME pointed at the throwaway dir,
    no XDG_* variables and nothing else inherited — because 1.3.0 has no `-c`
    flag, so isolating HOME is the only way to point btop at a candidate config.
    """
    try:
        pid, fd = pty.fork()
    except OSError:
        return None
    if pid == 0:  # pragma: no cover — the child never returns
        env = {
            "HOME": str(home),
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
        }
        try:
            os.execve(binary, [binary], env)
        finally:
            os._exit(127)

    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        out = b""
        sent = False
        deadline = time.time() + wait
        while True:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                # Answer cursor-position queries or btop waits on us.
                if b"\x1b[6n" in chunk:
                    try:
                        os.write(fd, b"\x1b[1;1R")
                    except OSError:
                        break
            if not sent and time.time() > deadline:
                try:
                    os.write(fd, b"q")
                    sent = True
                except OSError:
                    break
                deadline = time.time() + QUIT_TIMEOUT
            elif sent and time.time() > deadline:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
                break
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        _, status = os.waitpid(pid, 0)

    return PtyRun(os.waitstatus_to_exitcode(status), out, sent)


# ---------------------------------------------------------------------------
# The value-diff check — pure
# ---------------------------------------------------------------------------


def _log_lines(log_text: str, key: str) -> tuple[str, ...]:
    return tuple(ln for ln in log_text.splitlines() if key in ln)


def rejections(
    candidate_text: str, after_text: str, log_text: str = ""
) -> list[Rejection]:
    """What the running btop did not keep, told by comparing values — not logs.

    - a modelled key btop saved back with a different value (its own default, on
      a rejected value), or
    - a key that vanished from a *rewritten* file — btop only writes keys it
      knows, so a vanished key is one it does not read. A file btop left alone
      is byte-identical, so nothing can "vanish" from it; the `#?` header is how
      a rewrite is told apart.
    """
    before = {ln.key: ln for ln in render.parse(candidate_text) if ln.key is not None}
    after = {ln.key: ln for ln in render.parse(after_text) if ln.key is not None}
    rewritten = after_text.startswith("#?")

    out: list[Rejection] = []
    for key, line in before.items():
        if key in after:
            if after[key].value != line.value:
                out.append(Rejection(
                    key, line.raw_value, after[key].raw_value,
                    _log_lines(log_text, key),
                ))
        elif rewritten:
            out.append(Rejection(
                key, line.raw_value, "", _log_lines(log_text, key)
            ))
    return out


# ---------------------------------------------------------------------------
# launch() — the live check
# ---------------------------------------------------------------------------


def launch(candidate_text: str, found: detect.Btop) -> LiveResult | None:
    """Start the real btop against the candidate and read what it saves back.

    `None` when btop is not installed. Takes ~5s (a 2.5s run plus the quit) —
    the wizard says so before it runs.
    """
    if not found.installed or not found.binary:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="devstuff-btop-") as tmp:
            home = Path(tmp)
            conf = home / ".config" / "btop" / CONFIG_FILE
            conf.parent.mkdir(parents=True)
            conf.write_text(candidate_text, encoding="utf-8")
            run = run_pty(found.binary, home)
            if run is None:  # pragma: no cover
                return None
            try:
                after = conf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                after = ""
            log_path = home / ".local" / "state" / "btop.log"
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                log_text = ""
    except OSError:
        return None

    output = render.strip_ansi(run.output.decode("utf-8", "replace"))
    started = run.sent_quit or len(run.output) > _STARTED_BYTES
    return LiveResult(
        started=started,
        exit_code=run.exit_code,
        output=output[-800:],
        rejections=rejections(candidate_text, after, log_text),
    )


def live_report(result: LiveResult | None, found: detect.Btop) -> Report:
    """Frame a `launch()` outcome as checks for the review screen."""
    if result is None:
        return Report(found.version, [])
    checks: list[Check] = []
    checks.append(Check(
        "btop starts with this config",
        result.started,
        "started and ran" if result.started else
        "btop did not start — " + escape(
            (result.output.strip().splitlines() or ["no output"])[-1]
        ),
    ))
    if result.started:
        if result.rejections:
            detail = "; ".join(
                f"{r.key} = {escape(r.wrote)} → btop kept "
                f"{escape(r.kept) if r.kept else '(key gone)'}"
                for r in result.rejections
            )
            checks.append(Check("btop kept what was written", False, detail))
        else:
            checks.append(Check(
                "btop kept what was written", True,
                "no value was reset — note this cannot prove enum values or the "
                "theme, which btop keeps silently",
            ))
    return Report(found.version, checks)


# ---------------------------------------------------------------------------
# verify() — offline, instant, before every save
# ---------------------------------------------------------------------------


def verify(cfg: BtopConfig, found: detect.Btop) -> Report:
    checks: list[Check] = []

    text = render.to_text(cfg, found)
    round_trip = render.matches(text, cfg, found)
    checks.append(Check(
        "written file matches the model",
        round_trip,
        "parses back identically" if round_trip else "the file says something else",
    ))

    checks.append(_types_check(cfg))
    checks.append(_enum_check(cfg))
    checks.append(_theme_check(cfg, found))
    checks.append(_boxes_check(cfg, found))
    checks.append(_quotable_check(cfg))
    checks.append(_known_keys_check(cfg, found))
    return Report(found.version, checks)


def _types_check(cfg: BtopConfig) -> Check:
    bad = []
    for key, setting in SETTINGS.items():
        value = getattr(cfg, key)
        if setting.kind == "bool":
            if not isinstance(value, bool):
                bad.append(f"{key}={value!r} (not a bool)")
        elif setting.kind == "int":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                bad.append(f"{key}={value!r} (not a non-negative integer)")
            elif setting.minimum is not None and value < setting.minimum:
                bad.append(f"{key}={value} (<{setting.minimum})")
            elif setting.maximum is not None and value > setting.maximum:
                bad.append(f"{key}={value} (>{setting.maximum})")
        elif not isinstance(value, str):
            bad.append(f"{key}={value!r} (not a string)")
    return Check(
        "types and ranges",
        not bad,
        "every value is the right type and in range"
        if not bad
        else f"btop would reset: {', '.join(bad)}",
    )


def _enum_check(cfg: BtopConfig) -> Check:
    bad = [
        f"{key}={getattr(cfg, key)!r}"
        for key, setting in SETTINGS.items()
        if setting.kind == "enum" and getattr(cfg, key) not in setting.choices
    ]
    return Check(
        "enum values",
        not bad,
        "every enum is a documented value"
        if not bad
        else f"btop keeps these silently and falls back: {', '.join(bad)}",
    )


def _theme_check(cfg: BtopConfig, found: detect.Btop) -> Check:
    if not found.themes:
        return Check(
            "theme exists", True,
            "the theme list could not be read — unverified, not wrong",
        )
    ok = cfg.color_theme in found.themes
    return Check(
        "theme exists",
        ok,
        "found on this machine"
        if ok
        else f"{cfg.color_theme!r} is not a theme here — btop renders Default without a word",
    )


def _boxes_check(cfg: BtopConfig, found: detect.Btop) -> Check:
    tokens = cfg.shown_boxes.split()
    if not tokens:
        return Check("shown_boxes", False, "empty — btop would show 'No boxes shown!'")
    allowed = set(BOXES)
    if found.keys is not None and "shown_gpus" in found.keys:
        allowed.update(GPU_BOXES)
    bad = [t for t in tokens if t not in allowed]
    if bad:
        why = " (this build has no GPU keys)" if any(t.startswith("gpu") for t in bad) else ""
        return Check(
            "shown_boxes", False,
            f"unknown {' '.join(bad)!r}{why} — btop resets the whole list silently",
        )
    return Check("shown_boxes", True, " ".join(tokens))


def _quotable_check(cfg: BtopConfig) -> Check:
    """A `"` or newline can never be written — a quoted value ends at the second
    quote, so `clock_format = "a"b"` comes back as `"a"` (measured)."""
    bad = []
    for key, setting in SETTINGS.items():
        if setting.kind == "bool" or setting.kind == "int":
            continue
        value = getattr(cfg, key)
        if isinstance(value, str) and ('"' in value or "\n" in value):
            bad.append(f"{key}={value!r}")
    return Check(
        "string values are writable",
        not bad,
        "no quotes or newlines inside values"
        if not bad
        else f"cannot be written: {', '.join(bad)}",
    )


def _known_keys_check(cfg: BtopConfig, found: detect.Btop) -> Check:
    """A key btop does not know is never written — so a *wanted* non-default for
    one is reported here rather than silently dropped."""
    if found.keys is None:
        return Check(
            "keys known to this btop", True,
            "btop's key set could not be read — every modelled key is offered",
        )
    wanted = [
        key for key, setting in SETTINGS.items()
        if getattr(cfg, key) != render.effective_default(setting, found)
    ]
    unknown = [key for key in wanted if key not in found.keys]
    return Check(
        "keys known to this btop",
        not unknown,
        "everything set is a key this build reads"
        if not unknown
        else f"this build does not know: {', '.join(unknown)} — those settings cannot be written",
    )


__all__ = [
    "Check",
    "LiveResult",
    "PtyRun",
    "Rejection",
    "Report",
    "launch",
    "live_report",
    "rejections",
    "run_pty",
    "verify",
]
