"""What the wizard can work out about the project before asking anything.

Every answer here is a *default*, never a decision: the wizard shows what was found
and lets the user override it. Nothing in this module writes.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.configure.commitizen.model import (
    CONFIG_FILES,
    VERSION_PROVIDERS,
    CommitizenConfig,
)

_VERSION_RE = re.compile(r"^\D*(\d+\.\d+(?:\.\d+)?)")


def git_root(start: Path | None = None) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start or Path.cwd(),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip() or ".")


def _has_commitizen_section(path: Path) -> bool:
    """Whether a config file actually carries commitizen settings.

    commitizen skips files without one, so a `pyproject.toml` in every Python project
    on disk must not count as "already configured" (`config.read_cfg`).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if path.suffix == ".json":
        try:
            return "commitizen" in json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return False
    if path.suffix in (".yaml", ".yml"):
        # Deliberately textual: pulling in a YAML parse here would make a malformed
        # file an exception instead of a "not configured".
        return bool(re.search(r"^commitizen\s*:", text, re.MULTILINE))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    return "commitizen" in data.get("tool", {}) or "commitizen" in data


def existing_configs(root: Path) -> list[Path]:
    """Every config file in `root` that commitizen would consider, in its search
    order. More than one is commitizen's "Multiple config files detected" warning —
    only the first is read."""
    return [
        root / name
        for name in CONFIG_FILES
        if (root / name).is_file() and _has_commitizen_section(root / name)
    ]


@dataclass
class Project:
    """What was found next to the user."""

    root: Path
    is_git: bool = False
    configs: list[Path] = field(default_factory=list)
    provider: str = "commitizen"
    version: str = "0.1.0"
    has_pyproject: bool = False
    changelog: str | None = None
    latest_tag: str | None = None

    @property
    def config(self) -> Path | None:
        """The file commitizen would actually read."""
        return self.configs[0] if self.configs else None


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _read_json(path: Path) -> dict:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _guess_provider(root: Path) -> tuple[str, str | None]:
    """(provider, version it reports). Marker files are checked in the model's
    declaration order, so `uv.lock` beats a bare `pyproject.toml`."""
    if (root / "Cargo.toml").is_file():
        data = _read_toml(root / "Cargo.toml")
        return "cargo", data.get("package", {}).get("version")
    if (root / "package.json").is_file():
        return "npm", _read_json(root / "package.json").get("version")
    if (root / "composer.json").is_file():
        return "composer", _read_json(root / "composer.json").get("version")
    if (root / "pyproject.toml").is_file():
        data = _read_toml(root / "pyproject.toml")
        if "version" in data.get("project", {}):
            provider = "uv" if (root / "uv.lock").is_file() else "pep621"
            return provider, data["project"]["version"]
        poetry = data.get("tool", {}).get("poetry", {})
        if "version" in poetry:
            return "poetry", poetry["version"]
    return "commitizen", None


def latest_tag(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _find_changelog(root: Path) -> str | None:
    for name in ("CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG", "docs/CHANGELOG.md"):
        if (root / name).is_file():
            return name
    return None


def inspect(start: Path | None = None) -> Project:
    """Look at the project around `start` (the current directory by default)."""
    cwd = (start or Path.cwd()).resolve()
    root = git_root(cwd) or cwd
    provider, version = _guess_provider(root)
    tag = latest_tag(root) if (root / ".git").exists() else None

    if version is None and tag:
        match = _VERSION_RE.match(tag)
        version = match.group(1) if match else None

    return Project(
        root=root,
        is_git=(root / ".git").exists(),
        configs=existing_configs(root),
        provider=provider,
        version=version or "0.1.0",
        has_pyproject=(root / "pyproject.toml").is_file(),
        changelog=_find_changelog(root),
        latest_tag=tag,
    )


def suggest(project: Project) -> CommitizenConfig:
    """A starting config that already matches the project it was run in."""
    cfg = CommitizenConfig()
    cfg.version_provider = project.provider
    cfg.version = project.version
    # PyPI rejects anything else, and a Python project that ships a non-PEP-440
    # version cannot be uploaded at all — so the scheme follows the provider.
    cfg.version_scheme = "pep440" if project.provider in ("pep621", "poetry", "uv") else "semver"
    cfg.tag_format = _guess_tag_format(project)
    cfg.changelog_file = project.changelog or cfg.changelog_file
    cfg.major_version_zero = project.version.startswith("0.")
    cfg.target = "pyproject.toml" if project.has_pyproject else ".cz.toml"
    if project.provider in VERSION_PROVIDERS and project.has_pyproject:
        # A Python project that already has a pyproject is the one case where the
        # existing file is a better home than a new dotfile next to it.
        cfg.target = "pyproject.toml"
    return cfg


def _guess_tag_format(project: Project) -> str:
    if project.latest_tag and not project.latest_tag.startswith("v"):
        return "$version"
    return "v$version"


def read_existing(path: Path) -> dict:
    """The commitizen settings already in a TOML config, or `{}`. Used to tell the
    user what they are about to replace — never to reconstruct wizard state (SD-6)."""
    if path.suffix != ".toml":
        return {}
    data = _read_toml(path)
    settings = data.get("tool", {}).get("commitizen")
    if settings is None:
        settings = data.get("commitizen")
    return settings if isinstance(settings, dict) else {}
