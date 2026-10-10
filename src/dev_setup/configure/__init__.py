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

A configurator may also expose:
   - `export(arg: str | None) -> str` — shell assignments printed by
     `devstuff configure <key> --export [ARG]`, for a wizard whose result is read by
     shell scripts rather than by a tool. Like `register: eval` functions, whatever it
     returns is the *entire* contents of stdout, so it must never be chatty.

Set `standalone=True` for a configurator with nothing to install — `proxmox` configures
devstuff's access to a remote host, not a local binary, so there is no catalog key to
check and no `devstuff install` to suggest.
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
    # True for a configurator with no catalog tool behind it. The install-state check
    # and the "install it first" hint are skipped rather than reporting a package that
    # was never meant to exist as missing.
    standalone: bool = False

    def load(self) -> ModuleType:
        return import_module(self.module)


CONFIGURATORS: dict[str, Configurator] = {
    "reptyr": Configurator(
        key="reptyr",
        label="reptyr",
        description="Temporary or persistent ptrace permissions and command-line usage",
        module="dev_setup.configure.reptyr.wizard",
    ),
    "ansible": Configurator(
        key="ansible",
        label="Ansible",
        description="ansible.cfg — inventory paths, forks, pipelining, become and vault",
        module="dev_setup.configure.ansible.wizard",
    ),
    "bat": Configurator(
        key="bat",
        label="bat",
        description="Theme, decorations, paging and the man-page/cat shell integration",
        module="dev_setup.configure.bat.wizard",
    ),
    "commitizen": Configurator(
        key="commitizen",
        label="Commitizen",
        description="Commit types, what each one bumps, tags and changelog sections",
        module="dev_setup.configure.commitizen.wizard",
    ),
    "docker": Configurator(
        key="docker",
        label="Docker",
        description="Log rotation, address pools and daemon behaviour in daemon.json",
        module="dev_setup.configure.docker.wizard",
    ),
    "lazygit": Configurator(
        key="lazygit",
        label="lazygit",
        description="Icons, diff pager, panels and git behaviour in config.yml",
        module="dev_setup.configure.lazygit.wizard",
    ),
    "proxmox": Configurator(
        key="proxmox",
        label="Proxmox",
        description="Connection profiles for the pve-* functions — hosts, auth and secrets",
        module="dev_setup.configure.proxmox.wizard",
        standalone=True,
    ),
    # The catalog key is hyphenated; the package cannot be, hence `precommit`.
    "pre-commit": Configurator(
        key="pre-commit",
        label="pre-commit",
        description="Which git hooks run, when they run, and what they are allowed to rewrite",
        module="dev_setup.configure.precommit.wizard",
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
