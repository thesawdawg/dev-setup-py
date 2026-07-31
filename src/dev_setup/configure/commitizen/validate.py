"""Check a candidate config by running the real `cz` against it.

The wizard can always show what it *believes* the config does — `render.bump_rows`
is derived from the same tables the emitter writes. This module answers the other
question: does commitizen agree? It builds a throwaway git repo, tags it at a known
version, replays one commit per bump level and reads back what `cz bump --dry-run`
says (SD-5).

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

from dev_setup.configure.commitizen import render
from dev_setup.configure.commitizen.model import (
    BREAKING_KEYWORD,
    MAJOR,
    MINOR,
    SAMPLE_BUMPS,
    SAMPLE_VERSION,
    CommitizenConfig,
)

TIMEOUT = 30
# `cz bump` exits 21 (NO_COMMITS_TO_BUMP) when nothing in the range warrants a
# release. For a type mapped to "no increment" that is the expected answer, not a
# failure — so it is read as "version unchanged" rather than as an error.
NO_COMMITS_TO_BUMP = 21

_BUMP_RE = re.compile(r"bump: version \S+ (?:→|->) (\S+)")


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
    return shutil.which("cz") is not None


def cz_version() -> str:
    result = _run(["cz", "version"], cwd=Path.cwd())
    return result.stdout.strip() if result and result.returncode == 0 else "unknown"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    env = {
        **os.environ,
        # The sandbox has no user identity of its own and must not borrow one from a
        # global gitconfig that may not exist in CI.
        "GIT_AUTHOR_NAME": "devstuff",
        "GIT_AUTHOR_EMAIL": "devstuff@localhost",
        "GIT_COMMITTER_NAME": "devstuff",
        "GIT_COMMITTER_EMAIL": "devstuff@localhost",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        # commitizen colours its output; a preview parses it.
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
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def tag_for(cfg: CommitizenConfig, version: str) -> str:
    """Render `tag_format` for a concrete version, the way commitizen would."""
    major, minor, patch = (version.split(".") + ["0", "0"])[:3]
    out = cfg.tag_format
    for name, value in (
        ("version", version),
        ("major", major),
        ("minor", minor),
        ("patch", patch),
    ):
        out = out.replace(f"${{{name}}}", value).replace(f"${name}", value)
    return out


def _preview_config(cfg: CommitizenConfig) -> CommitizenConfig:
    """The candidate, rewired to read its version from the sandbox instead of from
    the user's project.

    Only the version *source* changes — every rule under test (bump_pattern,
    bump_map, schema_pattern, version_scheme, tag_format, the changelog maps) is
    emitted verbatim, so what the sandbox exercises is the part the wizard generated.
    """
    from dataclasses import replace

    return replace(
        cfg,
        version_provider="commitizen",
        version=SAMPLE_VERSION,
        version_files=[],
        # A bump that also rewrites the changelog would be testing the template
        # engine, not the rules — and `--dry-run` is slower for it.
        update_changelog_on_bump=False,
    )


@contextmanager
def sandbox(cfg: CommitizenConfig):
    """A throwaway git repo holding the candidate config, tagged at 1.4.2.

    Yields the repo path, or `None` if it could not be built — a machine without git
    degrades to the offline table rather than to a traceback.
    """
    preview = _preview_config(cfg)
    with tempfile.TemporaryDirectory(prefix="devstuff-cz-") as tmp:
        root = Path(tmp)
        (root / ".cz.toml").write_text(render.to_toml(preview), encoding="utf-8")
        # A file to commit: some git versions refuse an initial empty tree in ways
        # that vary, and a real blob costs nothing.
        (root / "README.md").write_text("sandbox\n", encoding="utf-8")
        steps = [
            ["git", "init", "-q"],
            ["git", "add", "."],
            ["git", "commit", "-q", "-m", "chore: sandbox"],
            ["git", "tag", tag_for(preview, SAMPLE_VERSION)],
        ]
        for step in steps:
            result = _run(step, cwd=root)
            if result is None or result.returncode != 0:
                yield None
                return
        yield root


def _bump(root: Path, message: str, base_tag: str) -> tuple[str | None, str]:
    """(resulting version, raw output) for one commit message, replayed from the tag."""
    reset = _run(["git", "reset", "-q", "--hard", base_tag], cwd=root)
    if reset is None or reset.returncode != 0:
        return None, "could not reset the sandbox"
    commit = _run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=root)
    if commit is None or commit.returncode != 0:
        return None, "could not create the sandbox commit"

    result = _run(["cz", "--config", ".cz.toml", "bump", "--dry-run", "--yes"], cwd=root)
    if result is None:
        return None, "cz did not run"
    output = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode == NO_COMMITS_TO_BUMP:
        return SAMPLE_VERSION, output
    if result.returncode != 0:
        return None, output
    match = _BUMP_RE.search(output)
    return (match.group(1) if match else None), output


def verify(cfg: CommitizenConfig) -> Report | None:
    """Run the candidate past the real binary. `None` when `cz` is not installed."""
    if not available():
        return None

    checks: list[Check] = []
    with sandbox(cfg) as root:
        if root is None:
            return Report(
                version=cz_version(),
                checks=[Check("sandbox", False, "could not build a temporary git repo")],
            )

        loaded = _run(["cz", "--config", ".cz.toml", "example"], cwd=root)
        if loaded is None or loaded.returncode != 0:
            detail = (loaded.stderr.strip() or loaded.stdout.strip()) if loaded else "cz did not run"
            # Nothing below can mean anything if the file will not load, so stop here
            # rather than reporting a cascade of failures with one cause.
            return Report(cz_version(), [Check("config loads", False, detail)])
        checks.append(Check("config loads", True, "cz read the file"))

        checks += _message_checks(cfg, root)
        checks += _bump_checks(cfg, root)

    return Report(cz_version(), checks)


def _message_checks(cfg: CommitizenConfig, root: Path) -> list[Check]:
    """`cz check` has to accept the wizard's own sample messages. It is the cheapest
    way to catch a `schema_pattern` that does not match the `message_template` — the
    two are generated separately and nothing else ties them together."""
    checks: list[Check] = []
    for label, message in (
        ("plain commit accepted", render.sample_message(cfg)),
        ("breaking commit accepted", render.sample_message(cfg, breaking=True)),
    ):
        result = _run(["cz", "--config", ".cz.toml", "check", "-m", message], cwd=root)
        if result is None:
            checks.append(Check(label, False, "cz did not run"))
            continue
        ok = result.returncode == 0
        detail = message.splitlines()[0] if ok else (result.stderr.strip() or result.stdout.strip())
        checks.append(Check(label, ok, detail))
    return checks


def _bump_checks(cfg: CommitizenConfig, root: Path) -> list[Check]:
    """One replay per *distinct* bump level rather than per type.

    Ten types would mean ten git commits and ten `cz` invocations to prove the same
    four rules; the mapping from type to level is our own table, and the levels are
    what commitizen is being asked about.
    """
    base_tag = tag_for(_preview_config(cfg), SAMPLE_VERSION)
    breaking = MINOR if cfg.major_version_zero else MAJOR
    first = next((t.key for t in cfg.selected()), "feat")

    cases: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for change_type in cfg.selected():
        if change_type.bump in seen:
            continue
        seen.add(change_type.bump)
        cases.append((
            f"{change_type.key}: … → {change_type.bump or 'no release'}",
            f"{change_type.key}: sandbox commit",
            SAMPLE_BUMPS[change_type.bump],
        ))
    cases.append((
        f"{first}!: … → {breaking}",
        f"{first}!: sandbox commit",
        SAMPLE_BUMPS[breaking],
    ))
    cases.append((
        f"{BREAKING_KEYWORD} footer → {breaking}",
        f"{first}: sandbox commit\n\n{BREAKING_KEYWORD}: it changed",
        SAMPLE_BUMPS[breaking],
    ))

    checks: list[Check] = []
    for label, message, expected in cases:
        actual, output = _bump(root, message, base_tag)
        if actual is None:
            checks.append(Check(label, False, output.splitlines()[-1] if output else "no output"))
        elif actual != expected:
            checks.append(Check(label, False, f"cz produced {actual}, expected {expected}"))
        else:
            suffix = " (no release)" if expected == SAMPLE_VERSION else ""
            checks.append(Check(label, True, f"{SAMPLE_VERSION} → {actual}{suffix}"))
    return checks


def changelog_preview(cfg: CommitizenConfig) -> str | None:
    """What `cz changelog` writes for one commit of every selected type.

    The sections and their order come out of commitizen itself, so this is the answer
    to "will my `change_type_map` actually produce those headings?" — a question the
    offline table can only answer from intent.
    """
    if not available():
        return None
    with sandbox(cfg) as root:
        if root is None:
            return None
        base_tag = tag_for(_preview_config(cfg), SAMPLE_VERSION)
        if _run(["git", "reset", "-q", "--hard", base_tag], cwd=root) is None:
            return None
        for change_type in cfg.selected():
            _run(
                ["git", "commit", "-q", "--allow-empty", "-m",
                 f"{change_type.key}(sandbox): {change_type.description.lower()}"],
                cwd=root,
            )
        first = next((t.key for t in cfg.selected()), "feat")
        _run(
            ["git", "commit", "-q", "--allow-empty", "-m", f"{first}!: a breaking change"],
            cwd=root,
        )
        result = _run(["cz", "--config", ".cz.toml", "changelog", "--dry-run"], cwd=root)
        if result is None or result.returncode != 0:
            return None
        return result.stdout.strip() or None


__all__ = [
    "Check",
    "Report",
    "available",
    "changelog_preview",
    "cz_version",
    "sandbox",
    "tag_for",
    "verify",
]
