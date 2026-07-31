"""Per-tool configuration wizards.

Installation generalises into a handful of mechanisms, which is why every tool is a
YAML record run by one `GenericTool`. Configuration does not: starship's config is a
TOML file of format strings and palettes, git's is a series of `git config` calls.
So configurators are Python modules registered by tool key here — the same
strategy-dispatch shape as `_INSTALLERS` in `generic.py` (see SD-1/SD-2 in
docs/specs/starship-config/stack-decisions.md).

**Adding a configurator**

1. Write a module exposing two callables:
   - `run(*, target: Path | None = None) -> object | None` — the interactive wizard.
     Return `None` if the user cancelled; write nothing until they confirm.
   - `config_path() -> Path` — where the tool actually reads its config.
2. Add one `Configurator` entry below, keyed by the tool's catalog key.

Nothing else changes: the picker, install-state check, `--path`/`--output` handling
and the post-install offer in `install_cmd.py` all read this table.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType


@dataclass(frozen=True)
class Configurator:
    key: str
    label: str
    description: str
    # Imported on demand, so `devstuff list` never pays for wizard imports.
    module: str

    def load(self) -> ModuleType:
        return import_module(self.module)


CONFIGURATORS: dict[str, Configurator] = {
    "commitizen": Configurator(
        key="commitizen",
        label="Commitizen",
        description="Commit types, what each one bumps, tags and changelog sections",
        module="dev_setup.configure.commitizen.wizard",
    ),
    "starship": Configurator(
        key="starship",
        label="Starship",
        description="Prompt style, colour palette, and which sections appear",
        module="dev_setup.configure.starship.wizard",
    ),
}


def get(key: str) -> Configurator | None:
    return CONFIGURATORS.get(key)


def has(key: str) -> bool:
    return key in CONFIGURATORS
