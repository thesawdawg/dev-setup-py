"""Check a candidate config by running the real `pre-commit` against it.

The wizard can always show what it *believes* the config does — `render.hook_rows` is
derived from the same tables the emitter writes. This module answers the other
question: does pre-commit agree?

There are three levels, deliberately separated by what they cost:

- `verify()` — `pre-commit validate-config` in a throwaway repo. Offline, milliseconds,
  so it runs at save time as well as on demand. Catches every schema error.
- `resolve()` — `pre-commit install-hooks`. Clones each repo and builds its
  environment: this is the only thing that proves the *hook ids* exist, because
  `validate-config` checks the file's shape and never looks inside a repo. Needs the
  network and takes minutes, so it is an explicit menu action and nothing else.
- `autoupdate()` — asks each repo what its latest tag is, so the shipped pins can be
  refreshed at the moment of use.

Every failure path returns `None` or a failed `Check`. A verification must never be
able to end the wizard, and it is never a gate on saving — the user's config is the
user's to save.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from dev_setup.configure.precommit import render
from dev_setup.configure.precommit.model import CONFIG_FILE, REPOS, PreCommitConfig

TIMEOUT = 60
# Cloning and building a dozen hook environments is genuinely slow the first time.
INSTALL_TIMEOUT = 600
AUTOUPDATE_TIMEOUT = 300

_UPDATED_RE = re.compile(r"\[(?P<url>\S+)\] updating (?P<old>\S+) -> (?P<new>\S+)")


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
    return shutil.which("pre-commit") is not None


def _run(
    cmd: list[str], cwd: Path, timeout: int = TIMEOUT
) -> subprocess.CompletedProcess[str] | None:
    env = {
        **os.environ,
        # The sandbox has no identity of its own and must not borrow one from a global
        # gitconfig that may not exist in CI.
        "GIT_AUTHOR_NAME": "devstuff",
        "GIT_AUTHOR_EMAIL": "devstuff@localhost",
        "GIT_COMMITTER_NAME": "devstuff",
        "GIT_COMMITTER_EMAIL": "devstuff@localhost",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "NO_COLOR": "1",
    }
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def version() -> str:
    result = _run(["pre-commit", "--version"], cwd=Path.cwd())
    if result is None or result.returncode != 0:
        return "unknown"
    # "pre-commit 4.6.1"
    return result.stdout.strip().split()[-1] if result.stdout.strip() else "unknown"


@contextmanager
def sandbox(cfg: PreCommitConfig):
    """A throwaway git repo holding the candidate config.

    Yields the repo path, or `None` if it could not be built — a machine without git
    degrades to the offline table rather than to a traceback. It is a real repo
    because `pre-commit` refuses to do most things outside one.
    """
    with tempfile.TemporaryDirectory(prefix="devstuff-precommit-") as tmp:
        root = Path(tmp)
        (root / CONFIG_FILE).write_text(render.to_yaml(cfg), encoding="utf-8")
        (root / "README.md").write_text("sandbox\n", encoding="utf-8")
        steps = [
            ["git", "init", "-q"],
            ["git", "add", "."],
            ["git", "commit", "-q", "-m", "sandbox"],
        ]
        for step in steps:
            result = _run(step, cwd=root)
            if result is None or result.returncode != 0:
                yield None
                return
        yield root


def verify(cfg: PreCommitConfig) -> Report | None:
    """Run the candidate past the real binary. `None` when pre-commit is not installed."""
    if not available():
        return None

    checks: list[Check] = []

    # Emitted YAML must parse back to what the model meant. This one needs no binary
    # and no sandbox, so it is checked first and always.
    round_trip = render.matches(render.to_yaml(cfg), cfg)
    checks.append(Check(
        "generated YAML matches the model",
        round_trip,
        "parsed back identically" if round_trip else "the emitted file says something else",
    ))

    with sandbox(cfg) as root:
        if root is None:
            checks.append(Check("sandbox", False, "could not build a temporary git repo"))
            return Report(version(), checks)

        result = _run(["pre-commit", "validate-config", CONFIG_FILE], cwd=root)
        if result is None:
            checks.append(Check("config is valid", False, "pre-commit did not run"))
            return Report(version(), checks)
        ok = result.returncode == 0
        detail = (
            f"{len(cfg.selected())} hooks across {len(cfg.by_repo())} repositories"
            if ok
            else (result.stdout.strip() or result.stderr.strip()).splitlines()[-1]
        )
        checks.append(Check("config is valid", ok, detail))
        if not ok:
            # Nothing below can mean anything if the file will not load.
            return Report(version(), checks)

        checks += _stage_checks(cfg, root)

    return Report(version(), checks)


def _stage_checks(cfg: PreCommitConfig, root: Path) -> list[Check]:
    """Every selected hook is reachable at the stage it declares.

    `pre-commit run --hook-stage X --all-files` on an empty repo exits 0 having run
    nothing, so what this actually reads is the *listing*: a hook whose stage is not
    in `default_install_hook_types` would never fire in real use, and that is a
    silently broken config rather than an error anyone would see.
    """
    checks: list[Check] = []
    installed = set(cfg.install_hook_types())
    for stage, keys in render.stage_rows(cfg):
        if stage == "manual":
            continue
        ok = stage in installed
        checks.append(Check(
            f"{stage} hooks will run",
            ok,
            f"{len(keys)} hook{'s' if len(keys) != 1 else ''}: {', '.join(keys)}"
            if ok
            else f"{stage} is missing from default_install_hook_types",
        ))
    return checks


def resolve(cfg: PreCommitConfig) -> Report | None:
    """Clone every repo and build its environments — the only check that proves the
    hook *ids* are real.

    `validate-config` reads the file's shape and never opens a repo, so a mistyped id
    passes it and then fails on the user's first commit with "No hook with id ...".
    Slow and network-bound, hence its own explicit menu action.
    """
    if not available():
        return None
    with sandbox(cfg) as root:
        if root is None:
            return Report(version(), [Check("sandbox", False, "could not build a temporary git repo")])
        result = _run(["pre-commit", "install-hooks"], cwd=root, timeout=INSTALL_TIMEOUT)
        if result is None:
            return Report(version(), [Check("hooks resolve", False, "pre-commit did not run (timed out?)")])
        output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode == 0:
            return Report(version(), [Check(
                "hooks resolve",
                True,
                f"every id in {len(cfg.by_repo())} repositories exists and its environment builds",
            )])
        return Report(version(), [Check(
            "hooks resolve", False, output.splitlines()[-1] if output else "no output"
        )])


def autoupdate(cfg: PreCommitConfig) -> dict[str, str] | None:
    """Ask each repo for its latest tag. Returns {repo key: rev} for the *whole* set,
    not only what changed, so the caller can pin everything to what it was told.

    `None` means the update could not run at all (no binary, no network, no git).

    Note that pre-commit's autoupdate takes the newest tag, which can be a
    prerelease — the wizard reports what changed rather than applying it silently.
    """
    if not available():
        return None
    with sandbox(cfg) as root:
        if root is None:
            return None
        result = _run(["pre-commit", "autoupdate"], cwd=root, timeout=AUTOUPDATE_TIMEOUT)
        if result is None or result.returncode != 0:
            return None
        try:
            parsed = yaml.safe_load((root / CONFIG_FILE).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(parsed, dict):
            return None
        by_url = {repo.url: key for key, repo in REPOS.items()}
        out: dict[str, str] = {}
        for repo in parsed.get("repos") or []:
            if not isinstance(repo, dict):
                continue
            key = by_url.get(repo.get("repo", ""))
            if key and isinstance(repo.get("rev"), str):
                out[key] = repo["rev"]
        return out


def install(root: Path, hook_types: list[str]) -> tuple[bool, str]:
    """Run `pre-commit install` in the user's own repo. (ok, message).

    This is the one call in the module that touches the user's project, and it is
    only ever reached from an explicit confirmation in the wizard.
    """
    cmd = ["pre-commit", "install"]
    for hook_type in hook_types:
        cmd += ["--hook-type", hook_type]
    result = _run(cmd, cwd=root)
    if result is None:
        return False, "pre-commit did not run"
    output = (result.stdout.strip() or result.stderr.strip()) or "no output"
    return result.returncode == 0, output


__all__ = [
    "Check",
    "Report",
    "autoupdate",
    "available",
    "install",
    "resolve",
    "sandbox",
    "verify",
    "version",
]
