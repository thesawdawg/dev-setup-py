"""The data behind the Docker daemon wizard. Everything else in the package reads
these tables.

`LOG_DRIVERS`, `SETTINGS` and `PRESETS` are ordered — declaration order is prompt
order and emission order. Adding a setting is one `Setting` record, and it reaches
the picker, any preset naming it, the emitter and the review screen with no other
edit.

**Everything here was measured against a real daemon, not recalled.** In particular
the per-driver `opts` sets: `dockerd --validate` accepts any log option for any
driver, and the daemon then refuses to start *every container* — so these tuples are
what the daemon actually accepted when each option was tried against each driver.
Two of them contradict the obvious guess: `local` does take `tag`/`labels`/`env`, and
`journald` does *not* take `max-size`/`max-file`/`compress`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "daemon.json"

# Where the daemon reads its config. The rootless daemon reads a different file, and
# `detect.py` decides which of these is in play — a rootless user writing to /etc
# would need sudo for a file their daemon never opens.
SYSTEM_PATH = Path("/etc/docker/daemon.json")
ROOTLESS_PATH = Path.home() / ".config" / "docker" / "daemon.json"


# ---------------------------------------------------------------------------
# Log drivers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogDriver:
    key: str
    label: str
    description: str
    # Log options this driver accepts. Measured by offering each option to each
    # driver and reading whether the daemon said "unknown log opt".
    opts: tuple[str, ...]
    # Whether the driver caps its own disk use given max-size/max-file.
    rotates: bool
    note: str = ""


_ROTATION_OPTS = ("max-size", "max-file", "compress")
_LABEL_OPTS = ("tag", "labels", "labels-regex", "env", "env-regex")

LOG_DRIVERS: dict[str, LogDriver] = {
    "json-file": LogDriver(
        key="json-file",
        label="json-file",
        description="Docker's default. `docker logs` works; log shippers can read the file.",
        opts=_ROTATION_OPTS + _LABEL_OPTS,
        rotates=True,
        note="Uncapped by default — this is the setting that fills disks.",
    ),
    "local": LogDriver(
        key="local",
        label="local",
        description="Same features, compact binary format, rotates by default.",
        opts=_ROTATION_OPTS + _LABEL_OPTS,
        rotates=True,
        note="Nothing outside Docker can read the files; use `docker logs`.",
    ),
    "journald": LogDriver(
        key="journald",
        label="journald",
        description="Hand everything to systemd's journal, which does its own rotation.",
        # Measured: journald rejects max-size/max-file/compress outright.
        opts=_LABEL_OPTS,
        rotates=False,
        note="Rotation is journald's business — set it in journald.conf, not here.",
    ),
    "syslog": LogDriver(
        key="syslog",
        label="syslog",
        description="Ship to a syslog daemon, local or remote.",
        opts=_LABEL_OPTS + ("syslog-address", "syslog-facility", "syslog-format"),
        rotates=False,
        note="Needs a reachable syslog endpoint; containers fail to start without one.",
    ),
    "none": LogDriver(
        key="none",
        label="none",
        description="Discard container output entirely.",
        opts=(),
        rotates=True,
        note="`docker logs` returns nothing at all. Rarely what anyone wants.",
    ),
}

DEFAULT_LOG_DRIVER = "json-file"

# Drivers that need a destination configured before they will accept a container.
# Offered, but never preselected by a preset.
NEEDS_ENDPOINT = frozenset({"syslog"})

# `docker info` reports the log *plugins*, and `none` is not one of them — it is
# built into the daemon. Measured: `docker run --log-driver none` works on a daemon
# whose Plugins.Log list has no "none" in it. So the plugin list is a set of drivers
# that certainly exist, not the complete set of valid ones, and the availability
# check has to allow for that or it reports a working driver as missing.
BUILTIN_DRIVERS = frozenset({"none"})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    description: str


GROUPS: dict[str, Group] = {
    "logging": Group("logging", "Logging", "What container output does to your disk"),
    "runtime": Group("runtime", "Runtime", "How the daemon behaves across restarts"),
    "network": Group("network", "Networking", "Address pools, DNS and the userland proxy"),
    "registry": Group("registry", "Registries", "Mirrors and internal registries"),
    "storage": Group("storage", "Storage", "Where images and containers live"),
    "security": Group("security", "Security", "Defaults applied to every container"),
    "observability": Group("observability", "Observability", "Metrics and debug output"),
}


@dataclass(frozen=True)
class Setting:
    """One decision, and the `daemon.json` key it becomes.

    `default` is the daemon's own default. A field equal to its default is omitted
    from the emitted file — a config that restates the defaults is noise, and it
    also freezes them, so a future Docker that changes a default would not reach
    this machine.
    """

    key: str
    json_key: str
    label: str
    description: str
    group: str
    kind: str  # bool | int | str | list | pools | logopts
    default: object = None
    why: str = ""


SETTINGS: dict[str, Setting] = {
    "log_driver": Setting(
        key="log_driver",
        json_key="log-driver",
        label="Log driver",
        description="Where container stdout/stderr goes",
        group="logging",
        kind="str",
        default=DEFAULT_LOG_DRIVER,
        why="The default writes uncapped JSON files under /var/lib/docker.",
    ),
    "log_opts": Setting(
        key="log_opts",
        json_key="log-opts",
        label="Log rotation",
        description="Size cap, how many files to keep, whether to compress",
        group="logging",
        kind="logopts",
        why="Without a cap a chatty container fills the disk and takes the host with it.",
    ),
    "live_restore": Setting(
        key="live_restore",
        json_key="live-restore",
        label="Keep containers running across daemon restarts",
        description="Containers survive `systemctl restart docker`",
        group="runtime",
        kind="bool",
        default=False,
        why="Only helps if it was already on before the restart that needs it.",
    ),
    "shutdown_timeout": Setting(
        key="shutdown_timeout",
        json_key="shutdown-timeout",
        label="Shutdown grace period (seconds)",
        description="How long the daemon waits for containers to stop",
        group="runtime",
        kind="int",
        default=15,
    ),
    "max_concurrent_downloads": Setting(
        key="max_concurrent_downloads",
        json_key="max-concurrent-downloads",
        label="Parallel layer downloads",
        description="How many image layers pull at once",
        group="runtime",
        kind="int",
        default=3,
    ),
    "address_pools": Setting(
        key="address_pools",
        json_key="default-address-pools",
        label="Default address pools",
        description="The subnets Docker carves user networks out of",
        group="network",
        kind="pools",
        why="Docker's default 172.17/16 collides with a lot of corporate VPNs.",
    ),
    "dns": Setting(
        key="dns",
        json_key="dns",
        label="DNS servers",
        description="Resolvers handed to every container",
        group="network",
        kind="list",
    ),
    "userland_proxy": Setting(
        key="userland_proxy",
        json_key="userland-proxy",
        label="Userland proxy",
        description="Route published ports through docker-proxy instead of iptables",
        group="network",
        kind="bool",
        default=True,
        why="Turning it off saves a process per published port; loopback publishing differs.",
    ),
    "registry_mirrors": Setting(
        key="registry_mirrors",
        json_key="registry-mirrors",
        label="Registry mirrors",
        description="Pull-through caches tried before Docker Hub",
        group="registry",
        kind="list",
    ),
    "insecure_registries": Setting(
        key="insecure_registries",
        json_key="insecure-registries",
        label="Insecure registries",
        description="Registries reachable over plain HTTP or with a self-signed certificate",
        group="registry",
        kind="list",
        why="Disables certificate verification for these hosts — internal registries only.",
    ),
    "data_root": Setting(
        key="data_root",
        json_key="data-root",
        label="Data root",
        description="Where images, containers and volumes live",
        group="storage",
        kind="str",
        default="/var/lib/docker",
        why="Moving this does not migrate what is already there.",
    ),
    "storage_driver": Setting(
        key="storage_driver",
        json_key="storage-driver",
        label="Storage driver",
        description="Leave unset unless you know you need a specific one",
        group="storage",
        kind="str",
        default="",
        why="Pinning the wrong one stops the daemon from starting.",
    ),
    "no_new_privileges": Setting(
        key="no_new_privileges",
        json_key="no-new-privileges",
        label="no-new-privileges by default",
        description="Stop processes in containers gaining privileges via setuid",
        group="security",
        kind="bool",
        default=False,
        why="Breaks images that rely on setuid helpers such as `sudo` inside the container.",
    ),
    "icc": Setting(
        key="icc",
        json_key="icc",
        label="Inter-container communication on the default bridge",
        description="Whether containers on the default bridge can reach each other",
        group="security",
        kind="bool",
        default=True,
        why="Turning it off does not affect user-defined networks, which is most compose files.",
    ),
    "metrics_addr": Setting(
        key="metrics_addr",
        json_key="metrics-addr",
        label="Metrics address",
        description="host:port to serve Prometheus metrics on",
        group="observability",
        kind="str",
        default="",
        why="Bind to 127.0.0.1 unless you mean to expose daemon internals.",
    ),
    "debug": Setting(
        key="debug",
        json_key="debug",
        label="Daemon debug logging",
        description="Verbose daemon logs",
        group="observability",
        kind="bool",
        default=False,
    ),
}

# Settings that are prompted for as free text/lists in the "advanced" step rather
# than being simple toggles.
TOGGLE_SETTINGS = tuple(k for k, s in SETTINGS.items() if s.kind == "bool")


# ---------------------------------------------------------------------------
# Log rotation defaults
# ---------------------------------------------------------------------------

# Every value in `log-opts` must be a *string*: `dockerd --validate` rejects a
# number outright ("cannot unmarshal number into ... of type string"), which is the
# one type error it does catch.
DEFAULT_MAX_SIZE = "10m"
DEFAULT_MAX_FILE = "3"

SIZE_RE = re.compile(r"^\d+(\.\d+)?[kmg]?$", re.IGNORECASE)


def valid_size(value: str) -> bool:
    """Whether a string is a size Docker will accept (`10m`, `512k`, `2g`)."""
    return bool(SIZE_RE.match(value.strip()))


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    values: dict[str, object] = field(default_factory=dict)


# The one setting everybody needs, and the reason this wizard exists.
_ROTATION: dict[str, object] = {
    "log_driver": "json-file",
    "log_max_size": DEFAULT_MAX_SIZE,
    "log_max_file": DEFAULT_MAX_FILE,
    "log_compress": False,
}

PRESETS: dict[str, Preset] = {
    "rotation": Preset(
        key="rotation",
        label="Log rotation only",
        description="Cap container logs and change nothing else. The safe minimum.",
        values=dict(_ROTATION),
    ),
    "workstation": Preset(
        key="workstation",
        label="Workstation",
        description="Log rotation, containers that survive a daemon restart, faster pulls.",
        values={
            **_ROTATION,
            "live_restore": True,
            "max_concurrent_downloads": 6,
        },
    ),
    "server": Preset(
        key="server",
        label="Server",
        description="Bigger log budget, live restore, metrics on loopback, tighter defaults.",
        values={
            **_ROTATION,
            "log_max_size": "50m",
            "log_max_file": "5",
            "log_compress": True,
            "live_restore": True,
            "no_new_privileges": True,
            "metrics_addr": "127.0.0.1:9323",
            "shutdown_timeout": 30,
        },
    ),
    "ci-runner": Preset(
        key="ci-runner",
        label="CI runner",
        description="Small logs, fast pulls, no live restore — the host is disposable.",
        values={
            **_ROTATION,
            "log_max_size": "5m",
            "log_max_file": "2",
            "live_restore": False,
            "max_concurrent_downloads": 10,
        },
    ),
    "journald": Preset(
        key="journald",
        label="Hand logs to systemd",
        description="journald owns container output and its own rotation.",
        values={"log_driver": "journald"},
    ),
    "corporate": Preset(
        key="corporate",
        label="Behind a corporate network",
        description="Log rotation plus address pools that avoid the usual VPN collisions.",
        values={
            **_ROTATION,
            "live_restore": True,
            "address_pools": [("10.201.0.0/16", 24), ("10.202.0.0/16", 24)],
        },
    ),
    "current": Preset(
        key="current",
        label="Whatever is on this machine now",
        description="Start from the existing daemon.json and adjust it.",
        values={},
    ),
    "empty": Preset(
        key="empty",
        label="Start from nothing",
        description="An empty config — every Docker default, explicitly.",
        values={},
    ),
}

DEFAULT_PRESET = "rotation"


# ---------------------------------------------------------------------------
# The config
# ---------------------------------------------------------------------------


@dataclass
class DockerConfig:
    preset: str = DEFAULT_PRESET

    log_driver: str = DEFAULT_LOG_DRIVER
    log_max_size: str = ""
    log_max_file: str = ""
    log_compress: bool = False
    log_extra: dict[str, str] = field(default_factory=dict)

    live_restore: bool = False
    shutdown_timeout: int = 15
    max_concurrent_downloads: int = 3

    address_pools: list[tuple[str, int]] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)
    userland_proxy: bool = True

    registry_mirrors: list[str] = field(default_factory=list)
    insecure_registries: list[str] = field(default_factory=list)

    data_root: str = "/var/lib/docker"
    storage_driver: str = ""

    no_new_privileges: bool = False
    icc: bool = True

    metrics_addr: str = ""
    debug: bool = False

    # Keys read from an existing daemon.json that this wizard does not model.
    # They are carried through to the emitted file untouched: daemon.json is a flat
    # JSON object with no comments and no ordering significance, so unlike a
    # pre-commit config it *can* be round-tripped without silent loss. Dropping a
    # key the user set by hand would be the worst thing this wizard could do.
    extra: dict[str, object] = field(default_factory=dict)

    # Where the daemon on this machine actually reads its config.
    target: Path = SYSTEM_PATH
    restart: bool = True

    # -- derived views ------------------------------------------------------

    def driver(self) -> LogDriver:
        return LOG_DRIVERS.get(self.log_driver) or LOG_DRIVERS[DEFAULT_LOG_DRIVER]

    def log_opts(self) -> dict[str, str]:
        """The `log-opts` mapping, filtered to what this driver actually accepts.

        The filter is the point. `dockerd --validate` accepts `max-size` under
        `journald` and the daemon then refuses to create any container at all
        ("unknown log opt"), so switching driver has to drop what no longer applies
        rather than leave it to fail later.
        """
        accepted = set(self.driver().opts)
        opts: dict[str, str] = {}
        if self.log_max_size and "max-size" in accepted:
            opts["max-size"] = self.log_max_size
        if self.log_max_file and "max-file" in accepted:
            opts["max-file"] = self.log_max_file
        if self.log_compress and "compress" in accepted:
            opts["compress"] = "true"
        for key, value in self.log_extra.items():
            if key in accepted:
                opts[key] = value
        return opts

    def dropped_log_opts(self) -> list[str]:
        """Options that would have been set but this driver does not take."""
        accepted = set(self.driver().opts)
        wanted = []
        if self.log_max_size:
            wanted.append("max-size")
        if self.log_max_file:
            wanted.append("max-file")
        if self.log_compress:
            wanted.append("compress")
        wanted += list(self.log_extra)
        return [opt for opt in wanted if opt not in accepted]

    def logs_are_capped(self) -> bool:
        """Whether container logs have an upper bound on disk.

        This is the question the whole wizard exists to answer yes to.
        """
        driver = self.driver()
        if driver.key in ("journald", "syslog", "none"):
            return True  # somebody else's rotation, or no logs at all
        return bool(self.log_opts().get("max-size"))

    def log_budget(self) -> str:
        """Worst-case disk per container, in human terms."""
        driver = self.driver()
        if driver.key == "none":
            return "nothing is kept"
        if not driver.rotates:
            return f"{driver.key}'s own rotation, not Docker's"
        opts = self.log_opts()
        size, count = opts.get("max-size"), opts.get("max-file")
        if not size:
            return "unbounded"
        try:
            files = int(count) if count else 1
        except ValueError:
            files = 1
        return f"{size} x {files} per container"

    def changed(self) -> dict[str, Setting]:
        """The settings that differ from the daemon's own defaults, in table order."""
        out: dict[str, Setting] = {}
        for key, setting in SETTINGS.items():
            if setting.kind == "logopts":
                if self.log_opts():
                    out[key] = setting
                continue
            value = getattr(self, key, None)
            if setting.kind in ("list", "pools"):
                if value:
                    out[key] = setting
            elif value != setting.default:
                out[key] = setting
        return out

    def warnings(self) -> list[str]:
        """Things that are legal, saved, and probably not what was meant.

        Every one of these is accepted by `dockerd --validate` — see `validate.py`
        for the ones that are outright broken rather than merely questionable.
        """
        out: list[str] = []
        if not self.logs_are_capped():
            out.append(
                "Container logs have no size cap — one noisy container can fill the disk."
            )
        dropped = self.dropped_log_opts()
        if dropped:
            out.append(
                f"The {self.log_driver} driver does not take {', '.join(dropped)}; "
                "they will not be written."
            )
        if self.log_driver in NEEDS_ENDPOINT:
            out.append(
                f"The {self.log_driver} driver needs a reachable endpoint — without one "
                "every container fails to start."
            )
        if self.insecure_registries:
            out.append(
                "Insecure registries skip certificate verification: "
                + ", ".join(self.insecure_registries)
            )
        if self.metrics_addr and not self.metrics_addr.startswith(("127.0.0.1", "localhost", "[::1]")):
            out.append(
                f"Metrics on {self.metrics_addr} are reachable off-host and are not "
                "authenticated."
            )
        if self.storage_driver:
            out.append(
                f"Pinning storage-driver to '{self.storage_driver}' stops the daemon "
                "starting if it is not usable on this kernel."
            )
        if self.data_root != SETTINGS["data_root"].default:
            out.append(
                f"Changing data-root to {self.data_root} does not move the images and "
                "containers already under the old one."
            )
        if not self.icc:
            out.append(
                "Disabling icc only affects the default bridge; compose projects use "
                "user-defined networks and are unaffected."
            )
        return out


__all__ = [
    "CONFIG_FILE",
    "DEFAULT_LOG_DRIVER",
    "DEFAULT_MAX_FILE",
    "DEFAULT_MAX_SIZE",
    "DEFAULT_PRESET",
    "GROUPS",
    "LOG_DRIVERS",
    "NEEDS_ENDPOINT",
    "PRESETS",
    "ROOTLESS_PATH",
    "BUILTIN_DRIVERS",
    "SETTINGS",
    "SYSTEM_PATH",
    "TOGGLE_SETTINGS",
    "DockerConfig",
    "Group",
    "LogDriver",
    "Preset",
    "Setting",
    "valid_size",
]
