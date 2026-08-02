"""Check a candidate lazygit config.

lazygit's own validation is lopsided, and the split decides what this module can and
cannot do. Measured against 0.62.2 by starting it under a pty with each config:

| config                          | lazygit's reaction                    |
|---------------------------------|---------------------------------------|
| a value of the wrong type       | **refuses to start**, names the file  |
| unparseable YAML                | **refuses to start**                  |
| an unknown key                  | starts, ignores it, says nothing      |
| an invalid enum value           | starts, falls back, says nothing      |

So `load()` — starting lazygit against the candidate — is a real and useful check,
and it is the *only* one lazygit performs. Everything below it in this module exists
because lazygit will not do it: the key set was verified at authoring time (see
`model.py`), the enum values are checked here, and a carried-over key that lazygit no
longer reads is reported rather than silently kept.

Starting lazygit needs a pty — it is a full-screen TUI and exits immediately without
one — so the live check is behind an explicit menu action rather than run at save
time. `verify()` is the offline half and runs before every save.
"""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dev_setup.configure.lazygit import detect, render
from dev_setup.configure.lazygit.model import SETTINGS, LazygitConfig

LAUNCH_TIMEOUT = 8.0

# lazygit's own words when it refuses a config.
_COMPLAINT = re.compile(r"(couldn't be parsed|unmarshal|cannot unmarshal|yaml:)", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


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


def available() -> bool:
    return shutil.which("lazygit") is not None


@contextmanager
def sandbox(cfg: LazygitConfig):
    """The candidate written to a throwaway git repo. Yields (repo, config path).

    A repository, because lazygit outside one shows a prompt rather than starting —
    which would make every launch look like a failure.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="devstuff-lazygit-") as tmp:
            root = Path(tmp)
            path = root / "config.yml"
            path.write_text(render.to_yaml(cfg), encoding="utf-8")
            init = subprocess.run(
                ["git", "init", "-q"], cwd=root, capture_output=True, check=False
            )
            if init.returncode != 0:  # pragma: no cover
                yield None, None
                return
            yield root, path
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        yield None, None


def launch(cfg: LazygitConfig) -> tuple[bool, str] | None:
    """Start lazygit against the candidate and quit. (started, output).

    `None` when lazygit is not installed. A pty is required — lazygit is a
    full-screen TUI and exits immediately when its output is a pipe, which would be
    indistinguishable from a rejected config.
    """
    if not available():
        return None
    with sandbox(cfg) as (root, path):
        if root is None or path is None:  # pragma: no cover
            return None
        return _spawn(root, path)


def _spawn(root: Path, path: Path) -> tuple[bool, str]:
    try:
        pid, fd = pty.fork()
    except OSError:  # pragma: no cover
        return False, "could not allocate a pty"
    if pid == 0:  # pragma: no cover — the child never returns
        os.environ.update(TERM="xterm", COLUMNS="80", LINES="24")
        try:
            os.chdir(root)
            os.execvp("lazygit", ["lazygit", "-ucf", str(path)])
        finally:
            os._exit(127)

    buffer = b""
    deadline = time.time() + LAUNCH_TIMEOUT
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buffer += chunk
        if _COMPLAINT.search(buffer.decode("utf-8", "replace")):
            break

    try:
        os.write(fd, b"q")
        time.sleep(0.3)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except OSError:  # pragma: no cover
        pass

    text = _ANSI.sub("", buffer.decode("utf-8", "replace"))
    complaint = _COMPLAINT.search(text)
    if complaint:
        line = next(
            (ln.strip() for ln in text.splitlines() if _COMPLAINT.search(ln)), text.strip()
        )
        return False, line[:200]
    return True, "lazygit started and read the config"


def verify(cfg: LazygitConfig, found: detect.Lazygit | None = None) -> Report:
    """Everything checkable without starting lazygit. Always returns a report."""
    checks: list[Check] = []

    text = render.to_yaml(cfg)
    round_trip = render.matches(text, cfg)
    checks.append(Check(
        "generated YAML matches the model",
        round_trip,
        "parsed back identically" if round_trip else "the emitted file says something else",
    ))

    checks.append(_choice_check(cfg))
    checks += _retired_checks(cfg)
    if found is not None:
        checks += _drift_checks(found)
    return Report(found.version if found else detect.version(), checks)


def _choice_check(cfg: LazygitConfig) -> Check:
    """Enum values lazygit would silently fall back from.

    lazygit accepts `nerdFontsVersion: "9"` and simply draws no icons, so nothing but
    this notices.
    """
    bad = []
    for key, setting in SETTINGS.items():
        if not setting.choices:
            continue
        value = getattr(cfg, key)
        if value not in setting.choices:
            bad.append(f"{setting.path}={value!r}")
    return Check(
        "values are ones lazygit accepts",
        not bad,
        "every value is a documented one"
        if not bad
        else f"lazygit would ignore and fall back: {', '.join(bad)}",
    )


def _retired_checks(cfg: LazygitConfig) -> list[Check]:
    return [
        Check(f"{path} is still read", False, why) for path, why in detect.retired_keys(cfg)
    ]


def _drift_checks(found: detect.Lazygit) -> list[Check]:
    """The model's defaults against this lazygit's.

    Only a warning-shaped check: the emitter omits a value equal to the modelled
    default, so drift means the wizard would stop writing a setting the user chose.
    """
    drift = detect.default_drift(found)
    if not drift:
        return [Check("defaults match this lazygit", True, "no drift")]
    return [
        Check(
            "defaults match this lazygit",
            False,
            "; ".join(f"{path}: wizard says {mine!r}, lazygit says {theirs!r}"
                      for path, mine, theirs in drift[:3]),
        )
    ]


__all__ = ["Check", "Report", "available", "launch", "sandbox", "verify"]
