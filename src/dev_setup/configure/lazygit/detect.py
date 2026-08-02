"""Ask lazygit about itself, and read any existing config.

`lazygit --print-config-dir` resolves the config directory, honouring
`--use-config-dir` and the XDG variables, so the search order is not reproduced here.

`lazygit --config` prints the defaults. It is used for *values* — the shipped default
of a setting — and deliberately **not** as a list of valid keys: it omits every
setting that has no default, so `git.paging.pager` and the whole `os:` section are
absent from it while being perfectly valid. See `model.py` for how keys were actually
verified.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.configure.lazygit import render
from dev_setup.configure.lazygit.model import (
    CONFIG_FILE,
    DEFAULT_CONFIG_DIR,
    RETIRED_KEYS,
    SETTINGS,
    LazygitConfig,
)

TIMEOUT = 20


@dataclass
class Lazygit:
    installed: bool = False
    version: str = ""
    config_dir: Path = DEFAULT_CONFIG_DIR
    path: Path = field(default_factory=lambda: DEFAULT_CONFIG_DIR / CONFIG_FILE)

    # lazygit's shipped defaults. Empty means "could not ask".
    defaults: dict = field(default_factory=dict)

    existing_text: str = ""
    existing: dict = field(default_factory=dict)
    parse_ok: bool = True
    generated: bool = False

    # Whether a Nerd Font appears to be installed. `None` means "cannot tell", which
    # is not the same as "no" — reused from the starship configurator's font gate.
    nerd_font: bool | None = None

    def has(self) -> bool:
        return bool(self.existing)


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
    return shutil.which("lazygit") is not None


def version() -> str:
    result = _run(["lazygit", "--version"])
    if result is None or result.returncode != 0:
        return ""
    # "commit=..., build date=..., version=0.62.2, os=linux, arch=amd64, ..."
    for part in result.stdout.split(","):
        key, _, value = part.strip().partition("=")
        if key == "version":
            return value
    return ""


def config_dir() -> Path:
    result = _run(["lazygit", "--print-config-dir"])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return DEFAULT_CONFIG_DIR
    return Path(result.stdout.strip().splitlines()[0])


def defaults() -> dict:
    """lazygit's shipped default config.

    Not a list of valid keys — see the module docstring — but authoritative about the
    *values* a setting defaults to, which is what stops the model's defaults drifting
    from the binary's.
    """
    result = _run(["lazygit", "--config"])
    if result is None or result.returncode != 0:
        return {}
    parsed, ok = render.load(result.stdout)
    return parsed if ok else {}


def _nerd_font() -> bool | None:
    """Reuse the starship configurator's font gate rather than a second one.

    It is allowed to answer "don't know": without fontconfig there is nothing to
    enumerate, and `None` means stay silent rather than warn.
    """
    from dev_setup.configure.starship import fonts

    try:
        return fonts.detect()
    except OSError:  # pragma: no cover — a gate must never raise
        return None


def inspect() -> Lazygit:
    found = Lazygit(installed=available())
    if found.installed:
        found.version = version()
        found.config_dir = config_dir()
        found.defaults = defaults()
    found.path = found.config_dir / CONFIG_FILE

    try:
        found.existing_text = found.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        found.existing_text = ""
    if found.existing_text:
        found.existing, found.parse_ok = render.load(found.existing_text)
        found.generated = found.existing_text.lstrip().startswith(render.GENERATED_HEADER)

    found.nerd_font = _nerd_font()
    return found


# ---------------------------------------------------------------------------
# Existing config → wizard state
# ---------------------------------------------------------------------------


def from_existing(found: Lazygit) -> LazygitConfig:
    """Read the config back into wizard state, preserving what is not modelled.

    The unmodelled part matters more here than anywhere else: a real lazygit config
    is likely to carry `customCommands` and a `keybinding` tree, which are the two
    things a user has most invested in and which this wizard does not touch.
    """
    cfg = LazygitConfig(preset="current", target=found.path)
    consumed: set[str] = set()

    for key, setting in SETTINGS.items():
        value = render.get(found.existing, setting.path, None)
        if value is None:
            continue
        # A value of the wrong type on disk is left alone rather than coerced: lazygit
        # itself refuses to start on one, so the user has a real problem the wizard
        # should not paper over by quietly reinterpreting it.
        is_bool = setting.kind == "bool" and isinstance(value, bool)
        is_int = (
            setting.kind == "int"
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        is_float = setting.kind == "float" and isinstance(value, (int, float)) and not isinstance(
            value, bool
        )
        if is_bool or is_int:
            setattr(cfg, key, value)
        elif is_float:
            setattr(cfg, key, float(value))
        elif setting.kind == "str":
            setattr(cfg, key, str(value))
        else:
            continue
        consumed.add(setting.path)

    cfg.extra = _without(found.existing, consumed)
    return cfg


def _without(tree: dict, paths: set[str], prefix: str = "") -> dict:
    """A copy of `tree` with the given dotted paths removed, dropping empty branches."""
    out: dict = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if path in paths:
            continue
        if isinstance(value, dict):
            pruned = _without(value, paths, path)
            if pruned:
                out[key] = pruned
        else:
            out[key] = value
    return out


def retired_keys(cfg: LazygitConfig) -> list[tuple[str, str]]:
    """Carried-over keys lazygit no longer reads. (path, why).

    lazygit ignores an unknown key rather than rejecting it, so these are invisible
    without being told.
    """
    found = []
    for path, why in RETIRED_KEYS.items():
        if render.get(cfg.extra, path, None) is not None:
            found.append((path, why))
    return found


def default_drift(found: Lazygit) -> list[tuple[str, object, object]]:
    """Settings whose modelled default disagrees with this lazygit's.

    The emitter omits a value equal to `Setting.default`, so a drift means the wizard
    would silently stop writing a setting the user did choose — or keep writing one
    they did not.
    """
    if not found.defaults:
        return []
    drift = []
    for setting in SETTINGS.values():
        actual = render.get(found.defaults, setting.path, _MISSING)
        if actual is _MISSING:
            # No default is not a disagreement: the dump omits settings that have none.
            continue
        if actual != setting.default:
            drift.append((setting.path, setting.default, actual))
    return drift


_MISSING = object()


def apply_preset(cfg: LazygitConfig, key: str, found: Lazygit) -> None:
    from dev_setup.configure.lazygit.model import PRESETS

    if key == "current":
        carried = from_existing(found)
        carried.preset = key
        _copy_into(cfg, carried)
        return
    fresh = LazygitConfig(preset=key, target=cfg.target, extra=dict(cfg.extra))
    if key != "empty":
        for name, value in PRESETS[key].values.items():
            setattr(fresh, name, value)
    _copy_into(cfg, fresh)


def _copy_into(target: LazygitConfig, source: LazygitConfig) -> None:
    for name in vars(source):
        setattr(target, name, getattr(source, name))


def suggest(found: Lazygit) -> LazygitConfig:
    """An existing config is the starting point; otherwise `recommended`, downgraded
    to `plain` when we can see there is no Nerd Font."""
    if found.has():
        return from_existing(found)
    cfg = LazygitConfig(target=found.path)
    apply_preset(cfg, "plain" if found.nerd_font is False else "recommended", found)
    return cfg


__all__ = [
    "Lazygit",
    "apply_preset",
    "available",
    "config_dir",
    "default_drift",
    "defaults",
    "from_existing",
    "inspect",
    "retired_keys",
    "suggest",
    "version",
]
