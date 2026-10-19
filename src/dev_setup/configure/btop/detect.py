"""Ask the installed `btop` about itself, and read any existing config.

Three things are asked of the machine rather than assumed:

- **Which keys this build knows.** `--default-config` (1.4.x) prints them; on
  1.3.0 — no such flag — a first-run probe in a throwaway HOME produces the same
  file. The set varies by version *and* build flags (the apt 1.4.6 build has GPU
  keys the upstream static 1.4.7 does not), so a table would be wrong.
- **Which themes exist.** `Default`/`TTY` are builtin; `*.theme` stems are
  scanned from the directories btop reads. A *path* is never a theme — measured:
  a full path outside the scanned dirs silently renders Default.
- **Whether a btop is running.** It reads the config only at start, and one with
  an in-app change overwrites external edits on exit.

`found.keys` is `None` when btop could not be asked — the difference between
"not installed" and "no keys" decides whether the wizard offers every setting or
only the ones this binary accepts.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.configure.btop import render
from dev_setup.configure.btop.model import (
    BOXES,
    CONFIG_FILE,
    GPU_BOXES,
    PRESETS,
    SETTINGS,
    BtopConfig,
    Setting,
)

TIMEOUT = 15
_VERSION = re.compile(r"btop version:\s*([0-9][\d.]*)")


@dataclass
class Btop:
    installed: bool = False
    binary: str = ""  # resolved realpath of the btop on PATH
    version: str = ""
    path: Path = field(default_factory=lambda: config_dir() / CONFIG_FILE)

    # From this binary's own defaults text; `keys` None means "could not ask".
    header: str = ""
    keys: frozenset[str] | None = None
    defaults: dict[str, str] = field(default_factory=dict)  # raw value text
    themes: tuple[str, ...] = ()

    existing_text: str = ""
    existing: dict[str, object] = field(default_factory=dict)
    existing_raw: dict[str, str] = field(default_factory=dict)
    # (key, raw value, why) for values btop itself would reset to the default.
    invalid_existing: list[tuple[str, str, str]] = field(default_factory=list)

    running: list[int] = field(default_factory=list)
    env_notes: list[str] = field(default_factory=list)

    def has(self) -> bool:
        return bool(self.existing_text.strip())


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def binary() -> str | None:
    path = shutil.which("btop")
    return str(Path(path).resolve()) if path else None


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    return (Path(xdg) if xdg else Path.home() / ".config") / "btop"


def config_file() -> Path:
    return config_dir() / CONFIG_FILE


def version(binary_path: str) -> str:
    result = _run([binary_path, "--version"])
    if result is None or result.returncode != 0:
        return ""
    # `btop version: \x1b[1m1.4.6\x1b[0m` on 1.4.x, `btop version: 1.3.0` on 1.3.0.
    m = _VERSION.search(render.strip_ansi(result.stdout))
    return m.group(1) if m else ""


def _parse_defaults(text: str) -> tuple[str, frozenset[str], dict[str, str]]:
    """(header, key set, raw defaults) out of a defaults dump."""
    header = text.splitlines()[0] if text.startswith("#?") else ""
    keys: set[str] = set()
    defaults: dict[str, str] = {}
    for line in render.parse(text):
        if line.key is not None:
            keys.add(line.key)
            defaults.setdefault(line.key, line.raw_value)
    return header, frozenset(keys), defaults


def _first_run_config(binary_path: str) -> str:
    """1.3.0 has no `--default-config`, but its first run writes the same file —
    so run it under a pty in a throwaway HOME and read what it wrote."""
    from dev_setup.configure.btop.validate import run_pty

    try:
        with tempfile.TemporaryDirectory(prefix="devstuff-btop-") as tmp:
            home = Path(tmp)
            run_pty(binary_path, home)
            return (home / ".config" / "btop" / CONFIG_FILE).read_text(
                encoding="utf-8", errors="replace"
            )
    except OSError:
        return ""


def defaults_text(
    binary_path: str, *, on_slow: Callable[[str], None] | None = None
) -> str:
    result = _run([binary_path, "--default-config"])
    if result is not None and result.returncode == 0 and result.stdout.startswith("#?"):
        return result.stdout
    if on_slow is not None:
        on_slow(
            "this btop has no --default-config — asking it for its defaults in "
            "a hidden terminal (~3s)…"
        )
    return _first_run_config(binary_path)


def themes(found: Btop) -> tuple[str, ...]:
    """The builtins plus every `*.theme` stem in the directories btop reads —
    binary-relative `../share/btop/themes` first, the distro fallbacks, then the
    user's own. A full path is never a value: btop does not apply one."""
    out = ["Default", "TTY"]
    dirs: list[Path] = []
    if found.binary:
        dirs.append((Path(found.binary).parent / ".." / "share" / "btop" / "themes").resolve())
    dirs += [
        Path("/usr/local/share/btop/themes"),
        Path("/usr/share/btop/themes"),
        config_dir() / "themes",
    ]
    for directory in dirs:
        try:
            stems = sorted(p.stem for p in directory.glob("*.theme"))
        except OSError:
            continue
        for stem in stems:
            if stem not in out:
                out.append(stem)
    return tuple(out)


def running(proc_root: Path = Path("/proc")) -> list[int]:
    """Pids of `btop` processes owned by this user."""
    me = os.getuid()
    pids: list[int] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != me:
                continue
            if (entry / "comm").read_text().strip() == "btop":
                pids.append(int(entry.name))
        except OSError:
            continue
    return sorted(pids)


# ---------------------------------------------------------------------------
# Existing file → wizard state
# ---------------------------------------------------------------------------


def _box_tokens(found: Btop) -> set[str]:
    tokens = set(BOXES)
    # gpuN is only real on a GPU build; "keys unknown" cannot promise it, so it
    # is only accepted when `shown_gpus` is in the key set.
    if found.keys is not None and "shown_gpus" in found.keys:
        tokens.update(GPU_BOXES)
    return tokens


def _coerce_existing(
    setting: Setting, value: object, found: Btop
) -> tuple[object, str]:
    """(value to carry into the wizard, why-it-was-reset-or-empty).

    Mirrors what btop itself does with a bad value: a rejected one becomes the
    effective default and is reported; a silently-kept one (an enum string btop
    does not validate, an unknown theme) is carried as-is so `verify` can flag it.
    """
    kind = setting.kind
    if kind == "bool":
        if isinstance(value, bool):
            return value, ""
        return render.effective_default(setting, found), "not true/false — btop resets it"
    if kind == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            if value < 0:
                return render.effective_default(setting, found), "negative — btop resets it"
            if setting.minimum is not None and value < setting.minimum:
                return (
                    render.effective_default(setting, found),
                    f"below {setting.minimum} — btop resets it",
                )
            if setting.maximum is not None and value > setting.maximum:
                return (
                    render.effective_default(setting, found),
                    f"above {setting.maximum} — btop resets it",
                )
            return value, ""
        return render.effective_default(setting, found), "not an integer — btop resets it"
    if kind == "boxes":
        if not isinstance(value, str):
            return render.effective_default(setting, found), "not a string — btop resets it"
        if value == "":
            # btop accepts it and shows "No boxes shown!" — kept so verify flags it.
            return value, ""
        bad = [t for t in value.split() if t not in _box_tokens(found)]
        if bad:
            return (
                render.effective_default(setting, found),
                f"unknown box {' '.join(bad)!r} — btop resets the whole list silently",
            )
        return value, ""
    if kind == "enum":
        if not isinstance(value, str):
            return render.effective_default(setting, found), "not a string — btop resets it"
        if value in setting.choices:
            return value, ""
        # Measured: a bad graph_symbol is rejected and reset, while
        # proc_sorting/temp_scale are kept verbatim — so the former becomes the
        # default and the latter is carried for `verify` to flag.
        if setting.key == "graph_symbol":
            return (
                render.effective_default(setting, found),
                "not a graph symbol — btop resets it",
            )
        return value, ""
    # str / theme — kept as written; an unknown theme or a bad clock_format is for
    # `verify` to flag, exactly as btop keeps them.
    if isinstance(value, str):
        return value, ""
    return render.effective_default(setting, found), "not a string — btop resets it"


def from_existing(found: Btop) -> BtopConfig:
    """Read the config on disk back into wizard state.

    Every modelled key maps onto a field; a value btop itself would reject is
    turned into the effective default and recorded in `invalid_existing` for the
    report — which is what btop does, measured, rather than an invention here.
    Keys the wizard does not model are left in the file untouched by the splice.
    """
    cfg = BtopConfig(preset="current", target=found.path)
    found.invalid_existing.clear()
    for key, setting in SETTINGS.items():
        if key not in found.existing:
            continue
        value, why = _coerce_existing(setting, found.existing[key], found)
        setattr(cfg, key, value)
        if why:
            found.invalid_existing.append(
                (key, found.existing_raw.get(key, repr(found.existing[key])), why)
            )
    return cfg


def apply_preset(cfg: BtopConfig, key: str, found: Btop) -> None:
    if key == "current":
        carried = from_existing(found)
        carried.preset = key
        _copy_into(cfg, carried)
        return
    fresh = BtopConfig(preset=key, target=cfg.target)
    for name, value in PRESETS[key].values.items():
        setattr(fresh, name, value)
    _copy_into(cfg, fresh)


def _copy_into(target: BtopConfig, source: BtopConfig) -> None:
    for name in vars(source):
        setattr(target, name, getattr(source, name))


def suggest(found: Btop) -> BtopConfig:
    """An existing config is the starting point; otherwise btop's defaults."""
    if found.has():
        return from_existing(found)
    cfg = BtopConfig(target=found.path)
    apply_preset(cfg, "defaults", found)
    return cfg


# ---------------------------------------------------------------------------
# inspect()
# ---------------------------------------------------------------------------


def inspect(*, on_slow: Callable[[str], None] | None = None) -> Btop:
    """Read the machine. `on_slow` is called once if the ~3s first-run probe is
    about to run, so the caller can say why the wizard paused."""
    found = Btop()
    found.binary = binary() or ""
    found.installed = bool(found.binary)
    found.path = config_file()

    if found.installed:
        found.version = version(found.binary)
        text = defaults_text(found.binary, on_slow=on_slow)
        if text:
            found.header, found.keys, found.defaults = _parse_defaults(text)
        found.themes = themes(found)
        found.running = running()

    try:
        found.existing_text = found.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        found.existing_text = ""
    if found.existing_text:
        found.existing = render.values(found.existing_text)
        found.existing_raw = {
            line.key: line.raw_value
            for line in render.parse(found.existing_text)
            if line.key is not None
        }

    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    if xdg:
        found.env_notes.append(f"XDG_CONFIG_HOME is set — btop reads {config_dir()}")
    return found


__all__ = [
    "Btop",
    "apply_preset",
    "binary",
    "config_dir",
    "config_file",
    "defaults_text",
    "from_existing",
    "inspect",
    "running",
    "suggest",
    "themes",
    "version",
]
