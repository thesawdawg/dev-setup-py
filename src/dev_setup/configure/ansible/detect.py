"""Look at the ansible installation and the project before asking anything.

Read-only, and every failure is an empty field rather than an exception.

Two things are asked of the binary rather than reproduced:

- **Which config file is in force.** `ansible-config dump` prints `CONFIG_FILE()`,
  which is the answer after the whole search order *and* the world-writable-directory
  rule have been applied. Reimplementing that here would be a second copy of it.
- **Which sections and keys exist.** `ansible-config list` is the source of the
  tables in `model.py`, and `validate.py` uses it again at run time.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.configure.ansible import render
from dev_setup.configure.ansible.model import (
    BUILTIN_PREFIX,
    CONFIG_FILE,
    SETTINGS,
    AnsibleConfig,
)

TIMEOUT = 30


@dataclass
class Project:
    installed: bool = False
    version: str = ""

    root: Path = field(default_factory=Path.cwd)
    # The file ansible says is in force right now — not necessarily the one in cwd.
    active_config: Path | None = None
    path: Path = field(default_factory=lambda: Path.cwd() / CONFIG_FILE)

    existing_text: str = ""
    existing: dict[str, dict[str, str]] = field(default_factory=dict)
    parse_ok: bool = True
    generated: bool = False

    # `ansible.cfg` in a world-writable directory is ignored outright. A config that
    # exists, parses and does nothing is the quietest failure ansible has.
    world_writable: bool = False

    playbooks: list[str] = field(default_factory=list)
    has_inventory: bool = False
    has_roles: bool = False
    has_collections: bool = False
    has_vault_file: bool = False

    callbacks: tuple[str, ...] = ()
    env_override: str = ""

    def has(self) -> bool:
        return bool(self.existing)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
            env={**os.environ, "NO_COLOR": "1", "ANSIBLE_NOCOLOR": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def available() -> bool:
    return shutil.which("ansible-config") is not None


def version() -> str:
    result = _run(["ansible", "--version"])
    if result is None or result.returncode != 0:
        return ""
    first = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    # "ansible [core 2.20.1]"
    return first.split("core", 1)[-1].strip(" ]") if "core" in first else first


def active_config(cwd: Path) -> Path | None:
    """The config ansible is actually using here, after its whole search order."""
    result = _run(["ansible-config", "dump", "--only-changed"], cwd=cwd)
    if result is None or result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("CONFIG_FILE()"):
            value = line.split("=", 1)[-1].strip()
            return None if value in ("None", "") else Path(value)
    return None


def stdout_callbacks() -> tuple[str, ...]:
    """Callback plugins this ansible can load, from `ansible-doc`.

    Empty means "could not ask", which keeps the availability check silent rather
    than wrong.
    """
    result = _run(["ansible-doc", "-t", "callback", "-l"])
    if result is None or result.returncode != 0:
        return ()
    names = []
    for line in result.stdout.splitlines():
        name = line.split(None, 1)[0].strip() if line.strip() else ""
        if name:
            names.append(name)
    return tuple(names)


def _world_writable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & stat.S_IWOTH)
    except OSError:
        return False


def inspect(root: Path | None = None) -> Project:
    cwd = root or Path.cwd()
    found = Project(installed=available(), root=cwd, path=cwd / CONFIG_FILE)

    if found.installed:
        found.version = version()
        found.active_config = active_config(cwd)
        found.callbacks = stdout_callbacks()

    found.world_writable = _world_writable(cwd)
    found.env_override = os.environ.get("ANSIBLE_CONFIG", "")

    try:
        found.existing_text = found.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        found.existing_text = ""
    if found.existing_text:
        found.existing, found.parse_ok = render.parse(found.existing_text)
        found.generated = found.existing_text.lstrip().startswith(render.GENERATED_HEADER)

    found.playbooks = sorted(
        path.name
        for path in cwd.glob("*.yml")
        if path.is_file() and path.name not in ("requirements.yml",)
    )[:10]
    found.has_inventory = any(
        (cwd / name).exists() for name in ("inventory", "inventories", "hosts")
    )
    found.has_roles = (cwd / "roles").is_dir()
    found.has_collections = (cwd / "collections").is_dir()
    found.has_vault_file = any(
        (cwd / name).exists() for name in (".vault-pass", ".vault_pass", ".vault-password")
    )
    return found


# ---------------------------------------------------------------------------
# Existing config → wizard state
# ---------------------------------------------------------------------------


def from_existing(found: Project) -> AnsibleConfig:
    """Read the ansible.cfg back into wizard state.

    Possible for the same reason it is for Docker and bat: INI is a flat
    section/key/value mapping. Anything not modelled is preserved in `extra`,
    including whole sections — a retired one is kept rather than deleted, and
    reported instead.
    """
    cfg = AnsibleConfig(preset="current", target=found.path)
    by_pair = {(s.section, s.ini_key): s for s in SETTINGS.values()}
    consumed: set[tuple[str, str]] = set()

    for section, body in found.existing.items():
        for key, raw in body.items():
            setting = by_pair.get((section, key))
            if setting is None:
                continue
            value = _coerce(setting.kind, raw)
            if value is None:
                continue
            setattr(cfg, setting.key, value)
            consumed.add((section, key))

    for section, body in found.existing.items():
        leftovers = {k: v for k, v in body.items() if (section, k) not in consumed}
        if leftovers:
            cfg.extra[section] = leftovers
    return cfg


# ansible's own truthy set. `configparser`'s BOOLEAN_STATES is close but not the
# same, and the difference would show up as a value the wizard reads back wrongly.
_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def _coerce(kind: str, raw: str) -> object | None:
    text = raw.strip()
    if kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        # Anything else is a non-empty string, which ansible reads as False — the
        # `pipelining = "True"` trap. Read it the way ansible does.
        return False
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            return None
    return text


def apply_preset(cfg: AnsibleConfig, key: str, found: Project) -> None:
    from dev_setup.configure.ansible.model import PRESETS

    if key == "current":
        carried = from_existing(found)
        carried.preset = key
        _copy_into(cfg, carried)
        return

    fresh = AnsibleConfig(preset=key, target=cfg.target, extra=dict(cfg.extra))
    if key != "empty":
        for name, value in PRESETS[key].values.items():
            setattr(fresh, name, value)
    _copy_into(cfg, fresh)


def _copy_into(target: AnsibleConfig, source: AnsibleConfig) -> None:
    for name in vars(source):
        setattr(target, name, getattr(source, name))


def suggest(found: Project) -> AnsibleConfig:
    """An existing config is the starting point; otherwise the project preset,
    adjusted to the directories that actually exist here."""
    if found.has():
        return from_existing(found)

    cfg = AnsibleConfig(target=found.path)
    apply_preset(cfg, "project", found)
    if not found.has_inventory:
        cfg.inventory = SETTINGS["inventory"].default  # type: ignore[assignment]
    if not found.has_roles:
        cfg.roles_path = ""
    if not found.has_collections:
        cfg.collections_path = ""
    if found.has_vault_file:
        cfg.vault_password_file = "./.vault-pass"
    return cfg


def unknown_callback(cfg: AnsibleConfig, found: Project) -> bool:
    """Whether the chosen output style is one this ansible cannot load.

    Silent when the plugin list could not be read.
    """
    if not found.callbacks or not cfg.stdout_callback:
        return False
    name = cfg.stdout_callback
    # `ansible-doc` prints fully-qualified names, so a short builtin name has to be
    # resolved before it can be found — otherwise every builtin looks unavailable.
    return name not in found.callbacks and f"{BUILTIN_PREFIX}{name}" not in found.callbacks


__all__ = [
    "Project",
    "active_config",
    "apply_preset",
    "available",
    "from_existing",
    "inspect",
    "stdout_callbacks",
    "suggest",
    "unknown_callback",
    "version",
]
