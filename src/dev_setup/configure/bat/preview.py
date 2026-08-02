"""Render the candidate config with the real `bat`, and check what bat will not.

This is the closest analogue in the repo to `configure/starship/preview.py`: the
config is written to a temp file, `BAT_CONFIG_PATH` points bat at it, and bat's own
output is shown. There is no approximation to drift from the real thing.

**And there is a check, because bat does not fail on a bad theme.** Measured: a
config naming a nonexistent theme makes bat print `[bat warning]: Unknown theme
'...', using default` on stderr and **exit 0**. Inside a pager that warning is
invisible, so a typo silently gives you the default theme forever. `check()` compares
every theme named against `bat --list-themes`, which is the only thing that catches
it. A bad `--style` component, by contrast, is a hard error and needs no help.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dev_setup.configure.bat import detect, render
from dev_setup.configure.bat.model import COMPONENTS, BatConfig

TIMEOUT = 20

# bat colours its own warnings even on stderr, so a complaint quoted into a check
# detail arrives wrapped in escapes. Strip them: the detail is read inside a Rich
# table that does its own styling.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Chosen to exercise what the decorations actually show: comments, strings, numbers,
# a keyword, and enough lines that `numbers` and `grid` have something to draw.
SAMPLE_NAME = "example.py"
SAMPLE = '''"""Parse a size like '10m' into bytes."""

UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}


def parse_size(text: str) -> int:
    value, unit = text[:-1], text[-1].lower()
    if unit not in UNITS:
        raise ValueError(f"unknown unit: {unit!r}")
    return int(float(value) * UNITS[unit])  # 10m -> 10485760
'''


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


@contextmanager
def sandbox(cfg: BatConfig):
    """A temp directory holding the candidate config and a sample file.

    Yields (config path, sample path), or (None, None) if it could not be built.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="devstuff-bat-") as tmp:
            root = Path(tmp)
            config = root / "config"
            config.write_text(render.to_text(cfg), encoding="utf-8")
            sample = root / SAMPLE_NAME
            sample.write_text(SAMPLE, encoding="utf-8")
            yield config, sample
    except OSError:  # pragma: no cover
        yield None, None


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def render_live(cfg: BatConfig, *, width: int = 88) -> tuple[str, str] | None:
    """bat's own output for the sample file under this config. (stdout, stderr).

    `None` when bat is not installed or did not run — every failure path here
    degrades to the offline description rather than ending the wizard.

    `--paging=never` and `--color=always` are forced on the command line, which beats
    the config file: a preview must not open a pager inside the wizard, and must not
    lose its colours for being piped. Everything else comes from the candidate.
    """
    if not detect.available():
        return None
    with sandbox(cfg) as (config, sample):
        if config is None or sample is None:  # pragma: no cover
            return None
        env = {
            **os.environ,
            "BAT_CONFIG_PATH": str(config),
            # bat's environment variables beat the config file, so any the user has
            # set must be cleared or the preview would show their value instead of
            # the candidate's.
            **{name: "" for name in detect.ENV_VARS if name != "BAT_CONFIG_PATH"},
            "COLUMNS": str(width),
            "NO_COLOR": "",
        }
        for name in detect.ENV_VARS:
            if name != "BAT_CONFIG_PATH":
                env.pop(name, None)
        result = _run(
            [
                "bat",
                "--paging=never",
                "--color=always",
                "--decorations=always",
                f"--terminal-width={width}",
                str(sample),
            ],
            env,
        )
    if result is None:
        return None
    return result.stdout, result.stderr


def check(cfg: BatConfig, found: detect.Bat | None = None) -> Report:
    """Everything checkable without a terminal. Always returns a report."""
    checks: list[Check] = []

    text = render.to_text(cfg)
    round_trip = render.matches(text, cfg)
    checks.append(Check(
        "generated config matches the model",
        round_trip,
        "parsed back identically" if round_trip else "the emitted file says something else",
    ))

    unknown_components = [c for c in cfg.components if c not in COMPONENTS]
    checks.append(Check(
        "style components are real",
        not unknown_components,
        f"{len(cfg.components)} components"
        if not unknown_components
        else f"bat would refuse to start: unknown {', '.join(unknown_components)}",
    ))

    if found is not None and found.themes:
        # The quiet one. A bad theme is a stderr warning and exit 0.
        missing = detect.unknown_themes(cfg, found)
        checks.append(Check(
            "themes exist",
            not missing,
            f"{', '.join(cfg.themes_in_use()) or 'bat default'}"
            if not missing
            else f"not in `bat --list-themes`: {', '.join(missing)} — bat would warn "
            "once on stderr and silently use the default",
        ))

    if detect.available():
        checks.append(_live_check(cfg))
    else:
        checks.append(Check(
            "bat accepts the config", True, "bat is not installed here — skipped, not failed"
        ))
    return Report(found.version if found else detect.version(), checks)


def _live_check(cfg: BatConfig) -> Check:
    """Run bat for real and read its stderr.

    A nonzero exit means bat refused the config outright. A zero exit with a warning
    on stderr is the interesting case — that is the silent-fallback path.
    """
    rendered = render_live(cfg)
    if rendered is None:
        return Check("bat accepts the config", False, "bat did not run")
    stdout, stderr = rendered
    complaint = next(
        (line for line in stderr.splitlines() if "warning" in line or "error" in line), ""
    )
    if complaint:
        return Check("bat accepts the config", False, _ANSI.sub("", complaint).strip())
    if not stdout.strip():
        return Check("bat accepts the config", False, "bat produced no output")
    return Check("bat accepts the config", True, f"rendered {len(stdout.splitlines())} lines")


def describe(cfg: BatConfig) -> list[str]:
    """The offline fallback: what the decorations would look like, in words.

    Used when bat is not installed. Deliberately a description rather than an ASCII
    mock-up — an approximation of a renderer is a thing that drifts from it, and the
    real output is one `devstuff install bat` away.
    """
    lines = []
    if cfg.components:
        for key in cfg.components:
            component = COMPONENTS.get(key)
            lines.append(f"{key:<18} {component.description if component else ''}")
    else:
        lines.append("plain              no decorations at all — just the highlighted text")
    return lines


__all__ = ["SAMPLE", "SAMPLE_NAME", "Check", "Report", "check", "describe", "render_live", "sandbox"]
