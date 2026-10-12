"""Asking the machine — every probe goes through generic._probe/_run so `-v`
covers them and nothing here writes state."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from dev_setup.generic import _probe


def config_file() -> Path:
    """Where syncthing keeps config.xml — asked of the binary, then the defaults."""
    try:
        r = _probe(["syncthing", "paths"], text=True, timeout=10)
    except OSError:
        r = None
    if r is not None and r.returncode == 0:
        lines = r.stdout.splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "Configuration file:" and i + 1 < len(lines):
                found = lines[i + 1].strip()
                if found:
                    return Path(found)

    xdg = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = xdg / "syncthing" / "config.xml"
    if path.exists():
        return path
    legacy = Path.home() / ".config" / "syncthing" / "config.xml"
    if legacy.exists():
        return legacy
    return path


def has_systemd() -> bool:
    return Path("/run/systemd/system").is_dir()


def user_session() -> bool:
    try:
        return _probe(["systemctl", "--user", "show-environment"], timeout=10).returncode == 0
    except OSError:
        return False


def service_active() -> bool:
    try:
        r = _probe(
            ["systemctl", "--user", "is-active", "syncthing.service"],
            text=True,
            timeout=10,
        )
    except OSError:
        return False
    return r.returncode == 0 and r.stdout.strip() == "active"


def linger_enabled() -> bool | None:
    """Whether sync survives logout — or None when the system cannot say."""
    try:
        r = _probe(
            ["loginctl", "show-user", getpass.getuser(), "-p", "Linger"],
            text=True,
            timeout=10,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    value = r.stdout.strip().removeprefix("Linger=").lower()
    if value in ("yes", "true", "1"):
        return True
    if value in ("no", "false", "0"):
        return False
    return None
