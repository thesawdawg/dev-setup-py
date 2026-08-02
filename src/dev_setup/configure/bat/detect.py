"""Ask the installed `bat` about itself, and read any existing config.

Two things are deliberately asked of the binary rather than assumed:

- **Where the config lives.** `bat --config-file` resolves it, honouring
  `BAT_CONFIG_PATH` and `BAT_CONFIG_DIR`. Reproducing that search order here would be
  a second implementation of it.
- **Which themes exist.** `bat --list-themes` is authoritative: a user can add themes
  with `bat cache --build`, and the table in `model.py` would then be wrong.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.configure.bat import render
from dev_setup.configure.bat.model import (
    BASHRC_BLOCK,
    DEFAULT_COMPONENTS,
    DEFAULT_CONFIG_PATH,
    SETTINGS,
    SHELL_BITS,
    THEMES,
    BatConfig,
)

TIMEOUT = 15


@dataclass
class Bat:
    installed: bool = False
    version: str = ""
    path: Path = DEFAULT_CONFIG_PATH
    # Empty means "could not ask", not "no themes exist" — the difference decides
    # whether an unrecognised theme name is reported as wrong or left alone.
    themes: tuple[str, ...] = ()

    existing_text: str = ""
    existing_flags: list[tuple[str, str]] = field(default_factory=list)
    existing_extra: list[str] = field(default_factory=list)
    generated: bool = False

    bashrc_patched: bool = False
    env_overrides: dict[str, str] = field(default_factory=dict)

    def has(self) -> bool:
        return bool(self.existing_text.strip())


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def available() -> bool:
    return shutil.which("bat") is not None


def version() -> str:
    result = _run(["bat", "--version"])
    if result is None or result.returncode != 0:
        return ""
    # "bat 0.26.1 (979ba22)"
    parts = result.stdout.strip().split()
    return parts[1] if len(parts) > 1 else ""


def themes() -> tuple[str, ...]:
    """Every theme this bat can load, including ones the user built themselves."""
    result = _run(["bat", "--list-themes", "--color=never"])
    if result is None or result.returncode != 0:
        return ()
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def config_file() -> Path:
    """Where bat says its config lives."""
    result = _run(["bat", "--config-file"])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return DEFAULT_CONFIG_PATH
    return Path(result.stdout.strip())


# bat's environment variables beat the config file, so a BAT_THEME in the user's
# shell makes a theme chosen here have no visible effect at all.
ENV_VARS = ("BAT_THEME", "BAT_STYLE", "BAT_PAGER", "BAT_PAGING", "BAT_CONFIG_PATH")


def inspect() -> Bat:
    found = Bat(installed=available())
    if found.installed:
        found.version = version()
        found.themes = themes()
        found.path = config_file()

    try:
        found.existing_text = found.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        found.existing_text = ""
    if found.existing_text:
        found.existing_flags, found.existing_extra = render.parse(found.existing_text)
        found.generated = found.existing_text.lstrip().startswith(render.GENERATED_HEADER)

    try:
        bashrc = (Path.home() / ".bashrc").read_text(encoding="utf-8", errors="replace")
        found.bashrc_patched = f"# {BASHRC_BLOCK}" in bashrc
    except OSError:
        found.bashrc_patched = False

    found.env_overrides = {name: os.environ[name] for name in ENV_VARS if name in os.environ}
    return found


# ---------------------------------------------------------------------------
# Existing config → wizard state
# ---------------------------------------------------------------------------


def from_existing(found: Bat) -> BatConfig:
    """Read the config back into wizard state.

    Possible for the same reason it is possible for Docker and not for pre-commit:
    the file is a flat list of the same options the model holds, so every line either
    maps to a field or is preserved in `extra` verbatim.
    """
    cfg = BatConfig(preset="current", target=found.path)
    by_flag = {setting.flag: setting.key for setting in SETTINGS.values()}

    for flag, value in found.existing_flags:
        key = by_flag.get(flag)
        if key is None:
            continue
        if key == "style":
            cfg.components = [] if value == "plain" else [
                part.strip() for part in value.split(",") if part.strip()
            ]
        elif key == "tabs":
            try:
                cfg.tabs = int(value)
            except ValueError:
                continue
        else:
            setattr(cfg, key, value)

    if cfg.theme_dark or cfg.theme_light:
        cfg.theme = "auto"
    cfg.extra = list(found.existing_extra)
    cfg.shell_bits = [key for key in SHELL_BITS] if found.bashrc_patched else []
    return cfg


def apply_preset(cfg: BatConfig, key: str, found: Bat) -> None:
    """Reset to a preset, keeping what a preset has no business deciding."""
    from dev_setup.configure.bat.model import PRESETS

    if key == "current":
        carried = from_existing(found)
        carried.preset = key
        _copy_into(cfg, carried)
        return

    fresh = BatConfig(
        preset=key,
        target=cfg.target,
        extra=list(cfg.extra),
        shell_bits=list(cfg.shell_bits),
    )
    if key == "empty":
        fresh.components = list(DEFAULT_COMPONENTS)
        _copy_into(cfg, fresh)
        return

    for name, value in PRESETS[key].values.items():
        setattr(fresh, name, list(value) if isinstance(value, list) else value)
    _copy_into(cfg, fresh)


def _copy_into(target: BatConfig, source: BatConfig) -> None:
    for name in vars(source):
        setattr(target, name, getattr(source, name))


def suggest(found: Bat) -> BatConfig:
    """An existing config is the starting point; otherwise the balanced preset."""
    if found.has():
        return from_existing(found)
    cfg = BatConfig(target=found.path)
    apply_preset(cfg, "balanced", found)
    return cfg


def known_themes(found: Bat) -> tuple[str, ...]:
    """The live list when it could be read, the shipped table otherwise."""
    return found.themes or tuple(THEMES)


def unknown_themes(cfg: BatConfig, found: Bat) -> list[str]:
    """Theme names this bat does not have.

    Silent when the list could not be read — an unanswerable question must not
    become a false accusation.
    """
    if not found.themes:
        return []
    return [name for name in cfg.themes_in_use() if name not in found.themes]


__all__ = [
    "ENV_VARS",
    "Bat",
    "apply_preset",
    "available",
    "config_file",
    "from_existing",
    "inspect",
    "known_themes",
    "suggest",
    "themes",
    "unknown_themes",
    "version",
]
