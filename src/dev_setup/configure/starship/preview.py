"""Render a candidate config with the real starship binary.

`starship prompt` is the same entry point the shell hook calls, so pointing
STARSHIP_CONFIG at a temp file and running it inside a throwaway project produces
the exact bytes the user's prompt will produce — ANSI and all (SD-3).

Every failure path returns None so the caller can fall back to the offline
renderer; a preview must never be able to end the wizard (NFR-4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dev_setup.configure.starship.model import StarshipConfig
from dev_setup.configure.starship.render import to_toml

TIMEOUT = 5

# Marker files that make the git/package/language sections render truthfully. A
# language module still needs its toolchain binary to report a version, so a
# selected language only shows up on a machine that has it — see SD-7.
MARKER_FILES = {
    "package.json": '{\n  "name": "api",\n  "version": "1.4.0"\n}\n',
    "pyproject.toml": '[project]\nname = "api"\nversion = "1.4.0"\n',
    "Cargo.toml": '[package]\nname = "api"\nversion = "1.4.0"\n',
    "go.mod": "module example.com/api\n\ngo 1.22\n",
    "composer.json": '{\n  "name": "example/api"\n}\n',
    "Gemfile": "source 'https://rubygems.org'\n",
    "pom.xml": "<project><artifactId>api</artifactId></project>\n",
}


def available() -> bool:
    return shutil.which("starship") is not None


@dataclass
class Sandbox:
    """A throwaway project plus somewhere to drop candidate configs."""

    project: Path
    config: Path
    cache: Path


@dataclass
class Rendered:
    """What starship printed. `right` is the right prompt, which starship renders
    only when asked for it separately (`prompt --right`)."""

    left: str
    right: str | None = None


@contextmanager
def sandbox() -> Iterator[Sandbox]:
    with tempfile.TemporaryDirectory(prefix="devstuff-starship-") as tmp:
        root = Path(tmp)
        # Named `api` and made the repo root so `truncate_to_repo` shows a plausible
        # project name instead of a temp path.
        project = root / "api"
        project.mkdir()
        for name, body in MARKER_FILES.items():
            (project / name).write_text(body, encoding="utf-8")
        _init_repo(project)
        cache = root / "cache"
        cache.mkdir()
        yield Sandbox(project=project, config=root / "candidate.toml", cache=cache)


def _init_repo(project: Path) -> None:
    """One staged and one untracked change, so git_branch and git_status both have
    something to say. Best-effort: without git the git sections simply don't render,
    which is the same degradation as previewing outside a repo."""
    try:
        subprocess.run(
            ["git", "init", "-q", "-b", "main"],
            cwd=project, check=True, capture_output=True, timeout=TIMEOUT,
        )
        subprocess.run(
            ["git", "add", "package.json"],
            cwd=project, check=True, capture_output=True, timeout=TIMEOUT,
        )
        (project / "notes.md").write_text("wip\n", encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        pass


def render(cfg: StarshipConfig, box: Sandbox, *, width: int = 80) -> Rendered | None:
    """The prompt for `cfg`, as raw ANSI. None if starship could not produce one."""
    try:
        box.config.write_text(to_toml(cfg), encoding="utf-8")
    except OSError:
        return None

    env = {
        **os.environ,
        "STARSHIP_CONFIG": str(box.config),
        "STARSHIP_CACHE": str(box.cache),
        # Not bash: for bash, starship wraps every escape in readline's `\[`/`\]`
        # non-printing markers, which are invisible inside a PS1 but print literally
        # when the output is displayed. `nu` is the dialect that adds no wrappers at
        # all, so what comes back is exactly the ANSI the terminal will draw.
        "STARSHIP_SHELL": "nu",
        # starship takes the logical path from PWD when it is set, and it is set — to
        # wherever the user invoked devstuff from. Without this the preview shows
        # their real directory rather than the sample project.
        "PWD": str(box.project),
        # Left alone deliberately: TERM/COLORTERM decide starship's colour depth, and
        # this subprocess inherits the same environment the shell hook will run in, so
        # whatever it picks here is what the real prompt will pick.
    }

    # Flag names have shifted across starship releases; fall back to the bare call
    # rather than losing the preview over an unrecognised option.
    attempts = [
        ["--status", "0", "--cmd-duration", "2400", "--jobs", "2",
         "--terminal-width", str(width)],
        ["--status", "0"],
        [],
    ]
    for args in attempts:
        left = _run(box, env, args)
        if left is None:
            continue
        right = _run(box, env, ["--right", *args]) if cfg.layout_spec.right_prompt else None
        return Rendered(left=left, right=right)
    return None


def _run(box: Sandbox, env: dict[str, str], args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["starship", "prompt", *args],
            cwd=box.project, env=env, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout
