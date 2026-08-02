"""Look at the Docker installation on this machine before asking anything.

Everything here is read-only and every failure is a `None`/empty field rather than
an exception: the wizard has to work on a host where the daemon is stopped, or
missing, or belongs to somebody else.

The interesting part is that the daemon can answer questions about itself. Which log
drivers exist is a *runtime* fact — `docker info` lists the plugins actually loaded
— so the wizard checks a chosen driver against that list rather than against a
hardcoded one.
"""

from __future__ import annotations

import getpass
import grp
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dev_setup.configure.docker.model import (
    DEFAULT_MAX_FILE,
    DEFAULT_MAX_SIZE,
    LOG_DRIVERS,
    PRESETS,
    ROOTLESS_PATH,
    SYSTEM_PATH,
    DockerConfig,
)

TIMEOUT = 15


@dataclass
class Docker:
    cli: bool = False
    daemon: bool = False
    rootless: bool = False
    version: str = ""
    storage_driver: str = ""
    log_driver: str = ""
    live_restore: bool = False
    data_root: str = ""
    containers: int = 0
    # The log drivers this daemon can actually load, straight from `docker info`.
    # Empty means "could not ask" — which is not the same as "none exist", so the
    # driver check stays silent rather than claiming a valid driver is invalid.
    log_plugins: tuple[str, ...] = ()

    path: Path = SYSTEM_PATH
    existing: dict[str, object] = field(default_factory=dict)
    existing_text: str = ""
    unreadable: bool = False
    invalid: bool = False

    in_group: bool = False
    group_exists: bool = False
    systemd: bool = False
    # The packaged unit passes `-H fd://`, and a `hosts` key in daemon.json then
    # collides with it and the daemon refuses to start. Detected, not assumed.
    unit_sets_host: bool = False

    def writable(self) -> bool:
        """Whether the config can be written without sudo."""
        target = self.path if self.path.exists() else self.path.parent
        return os.access(target, os.W_OK) if target.exists() else False


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


def _info() -> dict[str, object] | None:
    """`docker info` as JSON, or None if the daemon is not answering."""
    result = _run(["docker", "info", "--format", "{{json .}}"])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _in_docker_group() -> tuple[bool, bool]:
    """(group exists, this user is in it).

    Membership is what makes `docker` usable without sudo, and it only takes effect
    in a *new* login session — which is why the wizard says so rather than just
    reporting the bit.
    """
    try:
        entry = grp.getgrnam("docker")
    except (KeyError, OSError):
        return False, False
    try:
        user = getpass.getuser()
    except (KeyError, OSError):  # pragma: no cover — no passwd entry
        return True, False
    if user in entry.gr_mem:
        return True, True
    try:
        return True, entry.gr_gid in os.getgroups()
    except OSError:  # pragma: no cover
        return True, False


def _unit_sets_host() -> tuple[bool, bool]:
    """(systemd manages docker, its ExecStart passes -H)."""
    result = _run(["systemctl", "cat", "docker.service"])
    if result is None or result.returncode != 0:
        return False, False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("ExecStart=") and (" -H " in stripped or "--host" in stripped):
            return True, True
    return True, False


def _read_existing(path: Path) -> tuple[dict[str, object], str, bool, bool]:
    """(parsed, raw text, unreadable, invalid)."""
    if not path.exists():
        return {}, "", False, False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Root-owned and not world-readable is a normal state, not an error.
        return {}, "", True, False
    try:
        parsed = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}, text, False, True
    if not isinstance(parsed, dict):
        return {}, text, False, True
    return parsed, text, False, False


def inspect() -> Docker:
    found = Docker(cli=shutil.which("docker") is not None)

    info = _info() if found.cli else None
    if info is not None:
        found.daemon = True
        found.version = str(info.get("ServerVersion") or "")
        found.storage_driver = str(info.get("Driver") or "")
        found.log_driver = str(info.get("LoggingDriver") or "")
        found.live_restore = bool(info.get("LiveRestoreEnabled"))
        found.data_root = str(info.get("DockerRootDir") or "")
        try:
            found.containers = int(info.get("Containers") or 0)
        except (TypeError, ValueError):  # pragma: no cover
            found.containers = 0
        plugins = info.get("Plugins")
        if isinstance(plugins, dict) and isinstance(plugins.get("Log"), list):
            found.log_plugins = tuple(str(p) for p in plugins["Log"])
        security = info.get("SecurityOptions")
        if isinstance(security, list):
            found.rootless = any("rootless" in str(opt) for opt in security)

    # A rootless daemon never reads /etc/docker/daemon.json, so writing there would
    # need sudo for a file nothing opens.
    found.path = ROOTLESS_PATH if found.rootless else SYSTEM_PATH
    found.existing, found.existing_text, found.unreadable, found.invalid = _read_existing(
        found.path
    )

    found.group_exists, found.in_group = _in_docker_group()
    found.systemd, found.unit_sets_host = _unit_sets_host()
    return found


# ---------------------------------------------------------------------------
# Existing config → wizard state
# ---------------------------------------------------------------------------

# daemon.json key → (config field, coercion). Anything not listed is preserved
# verbatim in `DockerConfig.extra` rather than dropped.
_SIMPLE: dict[str, tuple[str, type]] = {
    "log-driver": ("log_driver", str),
    "live-restore": ("live_restore", bool),
    "shutdown-timeout": ("shutdown_timeout", int),
    "max-concurrent-downloads": ("max_concurrent_downloads", int),
    "userland-proxy": ("userland_proxy", bool),
    "data-root": ("data_root", str),
    "storage-driver": ("storage_driver", str),
    "no-new-privileges": ("no_new_privileges", bool),
    "icc": ("icc", bool),
    "metrics-addr": ("metrics_addr", str),
    "debug": ("debug", bool),
}

_LISTS = {
    "dns": "dns",
    "registry-mirrors": "registry_mirrors",
    "insecure-registries": "insecure_registries",
}


def from_existing(found: Docker) -> DockerConfig:
    """Read the daemon.json on disk back into wizard state.

    pre-commit's configurator deliberately refuses to do this, because a YAML config
    carries comments, ordering and constructs the model has no room for. daemon.json
    has none of that: it is a flat JSON object, so a faithful round-trip is possible
    — and anything unrecognised is kept in `extra` and written back out untouched,
    so "faithful" means it, rather than meaning "the parts we understood".
    """
    cfg = DockerConfig(preset="current", target=found.path)
    raw = found.existing

    for json_key, (field_name, kind) in _SIMPLE.items():
        if json_key not in raw:
            continue
        value = raw[json_key]
        try:
            if kind is bool and isinstance(value, bool):
                setattr(cfg, field_name, value)
            elif kind is int and isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(cfg, field_name, int(value))
            elif kind is str and isinstance(value, str):
                setattr(cfg, field_name, value)
            else:
                continue
        except (TypeError, ValueError):  # pragma: no cover
            continue

    for json_key, field_name in _LISTS.items():
        value = raw.get(json_key)
        if isinstance(value, list):
            setattr(cfg, field_name, [str(item) for item in value])

    opts = raw.get("log-opts")
    if isinstance(opts, dict):
        for key, value in opts.items():
            text = str(value)
            if key == "max-size":
                cfg.log_max_size = text
            elif key == "max-file":
                cfg.log_max_file = text
            elif key == "compress":
                cfg.log_compress = text.lower() == "true"
            else:
                cfg.log_extra[str(key)] = text

    pools = raw.get("default-address-pools")
    if isinstance(pools, list):
        for item in pools:
            if isinstance(item, dict) and "base" in item and "size" in item:
                try:
                    cfg.address_pools.append((str(item["base"]), int(item["size"])))
                except (TypeError, ValueError):  # pragma: no cover
                    continue

    known = set(_SIMPLE) | set(_LISTS) | {"log-opts", "default-address-pools"}
    cfg.extra = {key: value for key, value in raw.items() if key not in known}
    return cfg


def apply_preset(cfg: DockerConfig, key: str, found: Docker) -> None:
    """Reset the config to a preset's values, keeping what is not a preset's business.

    `extra` and `target` survive: a preset chooses settings, it does not decide to
    throw away keys the wizard never modelled.
    """
    preset = PRESETS[key]
    fresh = DockerConfig(preset=key, target=cfg.target, extra=dict(cfg.extra))

    if key == "current":
        carried = from_existing(found)
        carried.preset = key
        carried.extra = dict(cfg.extra) or carried.extra
        _copy_into(cfg, carried)
        return
    if key == "empty":
        _copy_into(cfg, fresh)
        cfg.log_max_size = ""
        cfg.log_max_file = ""
        return

    for name, value in preset.values.items():
        if name == "log_max_size":
            fresh.log_max_size = str(value)
        elif name == "log_max_file":
            fresh.log_max_file = str(value)
        elif name == "address_pools":
            fresh.address_pools = [(base, size) for base, size in value]  # type: ignore[misc]
        else:
            setattr(fresh, name, value)
    _copy_into(cfg, fresh)


def _copy_into(target: DockerConfig, source: DockerConfig) -> None:
    for name in vars(source):
        setattr(target, name, getattr(source, name))


def suggest(found: Docker) -> DockerConfig:
    """The config the wizard opens with.

    An existing daemon.json is the starting point when there is one — replacing a
    hand-tuned file with a preset by default would be the wrong first move. A host
    with no config at all starts from log rotation, which is the setting the absence
    of a config most reliably means is missing.
    """
    if found.existing:
        cfg = from_existing(found)
        if not cfg.logs_are_capped() and cfg.driver().rotates:
            # The overwhelmingly common case: a daemon.json that sets something else
            # and leaves logs uncapped.
            cfg.log_max_size = DEFAULT_MAX_SIZE
            cfg.log_max_file = DEFAULT_MAX_FILE
        return cfg

    cfg = DockerConfig(target=found.path)
    apply_preset(cfg, "rotation", found)
    return cfg


def unknown_driver(cfg: DockerConfig, found: Docker) -> bool:
    """Whether the chosen driver is one this daemon cannot load.

    Silent when the daemon could not be asked — an unanswerable question must not
    become a false accusation.
    """
    if not found.log_plugins:
        return False
    return cfg.log_driver not in found.log_plugins and cfg.log_driver in LOG_DRIVERS


__all__ = [
    "Docker",
    "apply_preset",
    "from_existing",
    "inspect",
    "suggest",
    "unknown_driver",
]
