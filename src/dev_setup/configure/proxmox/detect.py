"""Read what is already on this machine: the profile file, and the client-side tools.

There is no Proxmox binary here to interrogate — the thing being configured is on the
other end of a network — so "detection" splits in two. This module covers the local half
(is there a config, does it parse, is `ssh`/`curl`/`sshpass` present, does each profile's
secret actually resolve). The remote half is `validate.selftest()`, which asks the node.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.catalog import CatalogError
from dev_setup.configure.proxmox import render
from dev_setup.configure.proxmox.model import CONFIG_PATH, Profile, ProxmoxConfig

#: Client-side tools, and what to do about each one if it is missing. None of these is a
#: devstuff catalog tool, so the remedy names the distro package — the same call
#: `whats-on-port` makes for iproute2.
CLIENT_TOOLS = {
    "ssh": "sudo apt-get install -y openssh-client",
    "curl": "sudo apt-get install -y curl",
    "sshpass": "sudo apt-get install -y sshpass",
}


@dataclass
class Found:
    path: Path = CONFIG_PATH
    exists: bool = False
    text: str = ""
    generated: bool = False
    error: str = ""
    config: ProxmoxConfig = field(default_factory=ProxmoxConfig)
    tools: dict[str, str] = field(default_factory=dict)

    def has(self) -> bool:
        return bool(self.config.profiles)

    def missing_tools(self) -> list[str]:
        return [name for name, path in self.tools.items() if not path]


def load_config(path: Path | None = None) -> ProxmoxConfig:
    """The profile file, or an empty config if there is none. Raises on a broken one."""
    target = path or CONFIG_PATH
    if not target.exists():
        return ProxmoxConfig(target=target)
    return render.load(target.read_text(encoding="utf-8"), source=target)


def inspect(path: Path | None = None) -> Found:
    target = path or CONFIG_PATH
    found = Found(path=target, tools={n: shutil.which(n) or "" for n in CLIENT_TOOLS})
    if not target.exists():
        found.config = ProxmoxConfig(target=target)
        return found

    found.exists = True
    try:
        found.text = target.read_text(encoding="utf-8")
    except OSError as exc:
        found.error = str(exc)
        return found

    found.generated = render.was_generated(found.text)
    try:
        found.config = render.load(found.text, source=target)
    except CatalogError as exc:
        # Reported, not raised: the wizard is how you fix a broken file, so it has to be
        # able to open in front of one.
        found.error = str(exc)
        found.config = ProxmoxConfig(target=target)
    return found


def secret_state(profile: Profile) -> tuple[str, str]:
    """(state, detail) for a profile's secret. States: ok, missing, insecure, none, n/a."""
    if not profile.needs_secret():
        return "n/a", "this profile authenticates with a key or the agent"
    if profile.secret_env:
        if os.environ.get(profile.secret_env):
            return "ok", f"${profile.secret_env} is set in this shell"
        return "missing", f"${profile.secret_env} is not set in this shell"
    if profile.secret_file:
        path = Path(profile.secret_file).expanduser()
        if not path.exists():
            return "missing", f"{path} does not exist"
        if not os.access(path, os.R_OK):
            return "missing", f"{path} is not readable"
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            return "insecure", f"{path} is mode {mode:o} — other users can read it"
        return "ok", str(path)
    return "none", "no secret_env or secret_file is set"
