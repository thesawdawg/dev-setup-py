"""The data every other module in this package reads: profiles, and the Proxmox
vocabulary the functions speak.

**Where this came from.** The repo's rule for configurators is *measure the tool, don't
recall it*. There was no Proxmox host to interrogate, so the next best source was used
instead: `pve-docs/api-viewer/apidoc.js`, the API schema Proxmox generates from its own
source (454 paths). Every path, query parameter, returned field and required privilege
below was extracted from it by script. The CLI forms come from the generated man pages
(`qm.1`, `pct.1`, `vzdump.1`, `pvesh.1`).

Four things that turned up there and would have been got wrong from memory:

- **`GET /nodes/{node}/apt/update` needs `Sys.Modify`, not `Sys.Audit`.** Listing pending
  updates is a read by any reasonable definition and Proxmox guards it as a write. A
  token scoped `PVEAuditor` — exactly what a careful person makes for a read-only
  integration — gets a 403 from `pve-updates` and from nothing else, so that operation
  carries the privilege and explains that specific failure itself.
- **`/cluster/resources?type=vm` returns containers too.** The `type` *filter* takes
  `vm`; the `type` *field* that comes back is `qemu` or `lxc`. One request therefore
  resolves name -> VMID -> node -> which binary to use, which is the whole of guest
  resolution.
- **`qm migrate` takes `--online`; `pct migrate` takes `--restart`** (and
  `--targetstorage` against `--target-storage`). Containers cannot live-migrate.
- **A container snapshot has no `vmstate`.** The `lxc` snapshot endpoint simply lacks the
  field the `qemu` one has — the API agreeing with `pct.1` that there is no RAM to save.

`OPERATIONS` is the single table of what the functions do. `functions.yaml` is checked
against it in both directions by `tests/test_configure_proxmox.py`, so an operation that
gains a parameter here and not there is a test failure rather than a runtime surprise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from dev_setup.catalog import CONFIG_DIR, CatalogError

CONFIG_PATH = CONFIG_DIR / "proxmox.yaml"
SECRETS_DIR = CONFIG_DIR / "secrets"
CONFIG_VERSION = 1

#: Where a function looks for its profile when no `profile` parameter was given.
PROFILE_ENV = "DEVSTUFF_PVE_PROFILE"
#: The documented way to approve a state change without a terminal (SD-7).
ASSUME_YES_ENV = "DEVSTUFF_PVE_ASSUME_YES"

TRANSPORTS = ("ssh", "api")
SSH_AUTH = ("agent", "key", "password")

#: `USER@REALM!TOKENNAME`. Proxmox splits on the last `!`, and the realm may not contain
#: one; the token name is what `pveum user token add` accepted.
TOKEN_ID_RE = re.compile(r"^[^\s!@]+@[^\s!@]+![A-Za-z0-9._-]+$")

#: Guest type (the `type` field of /cluster/resources) -> the CLI that manages it.
BINARIES = {"qemu": "qm", "lxc": "pct"}

#: Migration, where the two binaries disagree (qm.1 / pct.1). A running VM migrates live
#: with --online; a running container cannot, and --restart reboots it on the far side.
MIGRATE_RUNNING_FLAG = {"qemu": "--online 1", "lxc": "--restart 1"}

#: vzdump.1: --mode snapshot | stop | suspend.
BACKUP_MODES = ("snapshot", "suspend", "stop")
#: qm.1 / pct.1 both take `shutdown` (ACPI, graceful) and `stop` (pull the cord).
POWER_MODES = ("shutdown", "stop")

# ---------------------------------------------------------------------------
# API paths, and what a token needs to call them
# ---------------------------------------------------------------------------

API: dict[str, str] = {
    "version": "/version",
    "cluster_status": "/cluster/status",
    "resources": "/cluster/resources",
    "backup_jobs": "/cluster/backup",
    "nodes": "/nodes",
    "node_status": "/nodes/{node}/status",
    "node_storage": "/nodes/{node}/storage",
    "apt_update": "/nodes/{node}/apt/update",
    "subscription": "/nodes/{node}/subscription",
    "snapshots": "/nodes/{node}/{kind}/{vmid}/snapshot",
    "storage_content": "/nodes/{node}/storage/{storage}/content",
}

#: Privilege each read needs, straight out of the schema's `permissions` block. "" means
#: the schema says `user: all` — any authenticated token may call it.
PRIVILEGES: dict[str, str] = {
    "version": "",
    "cluster_status": "Sys.Audit on /",
    "resources": "",
    "backup_jobs": "Sys.Audit on /",
    "nodes": "",
    "node_status": "Sys.Audit on /nodes/<node>",
    # `user: all`, but each row is filtered by Datastore.Audit on that storage, so a
    # narrow token gets a short list rather than a refusal.
    "node_storage": "",
    # Measured, and the one that surprises people. See the module docstring.
    "apt_update": "Sys.Modify on /nodes/<node>",
    "subscription": "",
    "snapshots": "VM.Audit on /vms/<vmid>",
    "storage_content": "Datastore.Audit on /storage/<storage>",
}


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    key: str
    name: str
    summary: str
    group: str
    #: Changes state on the node: SSH only, and confirmed before it runs (FR-16, FR-19).
    writes: bool
    #: Declared parameter names, in order. Every function's last parameter is `profile`.
    params: tuple[str, ...]
    #: Keys into `API` this operation reads. Empty for a pure write.
    reads: tuple[str, ...] = ()
    #: The command a user would type by hand — the thing these functions exist to teach.
    teaches: str = ""
    note: str = ""

    @property
    def privileges(self) -> tuple[str, ...]:
        """Distinct privileges an API token needs for this operation's reads."""
        seen = [PRIVILEGES[key] for key in self.reads if PRIVILEGES.get(key)]
        return tuple(dict.fromkeys(seen))


def _op(*args: Any, **kwargs: Any) -> tuple[str, Operation]:
    op = Operation(*args, **kwargs)
    return op.key, op


#: Declaration order is the order `devstuff functions list` shows them in, so it runs
#: from "what is going on" through to the things that change something.
OPERATIONS: dict[str, Operation] = dict([
    _op(
        key="pve-check",
        name="Proxmox Check Profile",
        summary="Check a Proxmox connection profile end to end and report what it can do",
        group="inventory",
        writes=False,
        params=("profile",),
        reads=("version",),
        teaches="pvesh get /version",
        note="Runs the same transport code the other functions use, so a pass means they work.",
    ),
    _op(
        key="pve-status",
        name="Proxmox Status",
        summary="Cluster and node health — quorum, uptime, load, memory and PVE version",
        group="inventory",
        writes=False,
        params=("profile",),
        reads=("version", "cluster_status", "nodes"),
        teaches="pvesh get /cluster/status --output-format json",
    ),
    _op(
        key="pve-guests",
        name="Proxmox Guests",
        summary="List VMs and containers with their node, state, uptime and resource use",
        group="inventory",
        writes=False,
        params=("filter", "profile"),
        reads=("resources",),
        teaches="pvesh get /cluster/resources --type vm --output-format json",
        note="One request covers both qemu and lxc across every node in the cluster.",
    ),
    _op(
        key="pve-storage",
        name="Proxmox Storage",
        summary="Storage pools per node — type, content, and how full each one is",
        group="storage",
        writes=False,
        params=("node", "profile"),
        reads=("nodes", "node_storage"),
        teaches="pvesh get /nodes/<node>/storage --output-format json",
        note="used_fraction comes from the API; nothing here computes a percentage.",
    ),
    _op(
        key="pve-updates",
        name="Proxmox Updates",
        summary="Pending package updates per node, and the subscription/repository state",
        group="storage",
        writes=False,
        params=("node", "profile"),
        reads=("nodes", "apt_update", "subscription"),
        teaches="pvesh get /nodes/<node>/apt/update --output-format json",
        note="Needs Sys.Modify over the API — a read-only token gets a 403 here and nowhere else.",
    ),
    _op(
        key="pve-backups",
        name="Proxmox Backups",
        summary="Backup archives on each backup-capable storage, newest first",
        group="backup",
        writes=False,
        params=("guest", "profile"),
        reads=("nodes", "node_storage", "storage_content", "resources"),
        teaches="pvesh get /nodes/<node>/storage/<storage>/content --content backup",
    ),
    _op(
        key="pve-snapshots",
        name="Proxmox Snapshots",
        summary="Snapshots of one guest, with their parent chain and whether RAM was saved",
        group="backup",
        writes=False,
        params=("guest", "profile"),
        reads=("resources", "snapshots"),
        teaches="qm listsnapshot <vmid>   ·   pct listsnapshot <vmid>",
    ),
    _op(
        key="pve-start",
        name="Proxmox Start Guest",
        summary="Start a VM or container by name or VMID",
        group="lifecycle",
        writes=True,
        params=("guest", "profile"),
        reads=("resources",),
        teaches="qm start <vmid>   ·   pct start <vmid>",
    ),
    _op(
        key="pve-stop",
        name="Proxmox Stop Guest",
        summary="Shut down a guest gracefully, or pull the cord with mode=stop",
        group="lifecycle",
        writes=True,
        params=("guest", "mode", "profile"),
        reads=("resources",),
        teaches="qm shutdown <vmid> --timeout 60   ·   qm stop <vmid>",
        note="shutdown is ACPI and can be ignored by a guest; stop is immediate and can corrupt.",
    ),
    _op(
        key="pve-restart",
        name="Proxmox Restart Guest",
        summary="Reboot a guest the way Proxmox does it, waiting for a clean shutdown",
        group="lifecycle",
        writes=True,
        params=("guest", "profile"),
        reads=("resources",),
        teaches="qm reboot <vmid>   ·   pct reboot <vmid>",
    ),
    _op(
        key="pve-migrate",
        name="Proxmox Migrate Guest",
        summary="Move a guest to another node — live for a VM, with a restart for a container",
        group="lifecycle",
        writes=True,
        params=("guest", "target", "profile"),
        reads=("resources", "nodes"),
        teaches="qm migrate <vmid> <node> --online 1   ·   pct migrate <vmid> <node> --restart 1",
        note="The flag differs by guest type: a running container cannot live-migrate.",
    ),
    _op(
        key="pve-snapshot",
        name="Proxmox Take Snapshot",
        summary="Take a snapshot of a guest before you change something",
        group="backup",
        writes=True,
        params=("guest", "snapname", "description", "profile"),
        reads=("resources",),
        teaches="qm snapshot <vmid> <name> --description <text> --vmstate 1",
        note="--vmstate is a qm-only option; containers have no RAM state to save.",
    ),
    _op(
        key="pve-rollback",
        name="Proxmox Roll Back Snapshot",
        summary="Roll a guest back to one of its snapshots, discarding changes since",
        group="backup",
        writes=True,
        params=("guest", "snapname", "profile"),
        reads=("resources", "snapshots"),
        teaches="qm rollback <vmid> <name>   ·   pct rollback <vmid> <name>",
        note="Everything written since the snapshot is gone, including later snapshots' base.",
    ),
    _op(
        key="pve-backup",
        name="Proxmox Back Up Guest",
        summary="Run vzdump for one guest against a backup storage",
        group="backup",
        writes=True,
        params=("guest", "storage", "mode", "profile"),
        reads=("resources", "node_storage"),
        teaches="vzdump <vmid> --mode snapshot --storage <storage> --compress zstd",
    ),
])

#: Group -> the heading `devstuff configure proxmox` prints it under.
GROUPS = {
    "inventory": "Inventory and health",
    "lifecycle": "Guest lifecycle",
    "backup": "Backups and snapshots",
    "storage": "Storage and maintenance",
}


def reads() -> list[Operation]:
    return [op for op in OPERATIONS.values() if not op.writes]


def writes() -> list[Operation]:
    return [op for op in OPERATIONS.values() if op.writes]


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@dataclass
class Profile:
    """One Proxmox connection. Never holds a secret — only where to find one (FR-5)."""

    name: str = ""
    transport: str = "ssh"
    host: str = ""
    #: The node's name inside the cluster, which is not always its hostname. Optional:
    #: anything cluster-wide works without it, and `pve-check` fills it in from the host.
    node: str = ""
    description: str = ""

    # ssh
    user: str = "root"
    port: int = 22
    auth: str = "agent"
    identity_file: str = ""
    #: Prefix remote commands with `sudo -n`. qm/pct/vzdump need root; a non-root user
    #: with sudo rights is the usual alternative to logging in as root.
    sudo: bool = False

    # api
    api_port: int = 8006
    token_id: str = ""
    verify_tls: bool = True

    # secret reference, exactly one of these
    secret_env: str = ""
    secret_file: str = ""

    def needs_secret(self) -> bool:
        return self.transport == "api" or (self.transport == "ssh" and self.auth == "password")

    def endpoint(self) -> str:
        if self.transport == "api":
            return f"https://{self.host}:{self.api_port}/api2/json"
        target = f"{self.user}@{self.host}"
        return target if self.port == 22 else f"{target}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        """Only what differs from the defaults, so a profile file stays readable and a
        future default change reaches profiles that never expressed an opinion."""
        out: dict[str, Any] = {"transport": self.transport, "host": self.host}
        relevant = _SSH_FIELDS if self.transport == "ssh" else _API_FIELDS
        for f in fields(self):
            if f.name in ("name", "transport", "host") or f.name in _TRANSPORT_FIELDS - relevant:
                continue
            value = getattr(self, f.name)
            if value != f.default:
                out[f.name] = value
        return out


_SSH_FIELDS = {"user", "port", "auth", "identity_file", "sudo"}
_API_FIELDS = {"api_port", "token_id", "verify_tls"}
_TRANSPORT_FIELDS = _SSH_FIELDS | _API_FIELDS

SUPPORTED_FIELDS = {f.name for f in fields(Profile)} - {"name"}


@dataclass
class ProxmoxConfig:
    profiles: dict[str, Profile] = field(default_factory=dict)
    default: str = ""
    #: The file this was read from, and the one a save writes back to.
    target: Path = CONFIG_PATH

    def get(self, name: str | None = None) -> Profile:
        """The profile a function should use: the one named, else $DEVSTUFF_PVE_PROFILE,
        else the default (FR-7). Raises rather than guessing."""
        wanted = name or self.default
        if not self.profiles:
            raise CatalogError(
                "No Proxmox profiles are configured yet.\n"
                "  Add one with:  devstuff configure proxmox"
            )
        if not wanted:
            raise CatalogError(
                "No default Proxmox profile is set, and none was named.\n"
                f"  Known profiles: {', '.join(sorted(self.profiles))}\n"
                "  Set a default with:  devstuff configure proxmox"
            )
        if wanted not in self.profiles:
            raise CatalogError(
                f"Unknown Proxmox profile: {wanted!r}\n"
                f"  Known profiles: {', '.join(sorted(self.profiles))}"
            )
        return self.profiles[wanted]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "default": self.default,
            "profiles": {name: p.to_dict() for name, p in self.profiles.items()},
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_profile(name: str, data: Any, *, source: Path | str = CONFIG_PATH) -> Profile:
    """Build a Profile, refusing anything malformed at load time.

    Same posture as the tool and function catalogs: an invalid file raises rather than
    silently degrading, because a profile that half-loads points at the wrong machine.
    """
    where = f"{source}: profile {name!r}"
    if not isinstance(data, dict):
        raise CatalogError(f"{where} must be a mapping")

    unknown = sorted(set(data) - SUPPORTED_FIELDS)
    if unknown:
        raise CatalogError(f"{where} has unknown field(s): {', '.join(unknown)}")

    profile = Profile(name=name)
    for f in fields(Profile):
        if f.name == "name" or f.name not in data:
            continue
        value = data[f.name]
        expected = type(f.default)
        if expected is bool and not isinstance(value, bool):
            raise CatalogError(f"{where}: {f.name} must be true or false, got {value!r}")
        if expected is int and not isinstance(value, int):
            raise CatalogError(f"{where}: {f.name} must be a number, got {value!r}")
        if expected is str and not isinstance(value, str):
            raise CatalogError(f"{where}: {f.name} must be a string, got {value!r}")
        setattr(profile, f.name, value)

    if profile.transport not in TRANSPORTS:
        raise CatalogError(
            f"{where}: transport must be one of {list(TRANSPORTS)}, got {profile.transport!r}"
        )
    if not profile.host:
        raise CatalogError(f"{where}: host is required")
    if profile.secret_env and profile.secret_file:
        raise CatalogError(f"{where}: set secret_env or secret_file, not both")

    if profile.transport == "ssh":
        if profile.auth not in SSH_AUTH:
            raise CatalogError(
                f"{where}: auth must be one of {list(SSH_AUTH)}, got {profile.auth!r}"
            )
        if profile.auth == "key" and not profile.identity_file:
            raise CatalogError(f"{where}: auth 'key' needs identity_file")
        if profile.auth == "password" and not (profile.secret_env or profile.secret_file):
            raise CatalogError(
                f"{where}: auth 'password' needs secret_env or secret_file "
                "(the password itself is never stored here)"
            )
    else:
        if not profile.token_id:
            raise CatalogError(f"{where}: transport 'api' needs token_id")
        if not TOKEN_ID_RE.match(profile.token_id):
            raise CatalogError(
                f"{where}: token_id must look like USER@REALM!TOKENNAME, "
                f"got {profile.token_id!r}"
            )
        if not (profile.secret_env or profile.secret_file):
            raise CatalogError(
                f"{where}: transport 'api' needs secret_env or secret_file "
                "(the token secret itself is never stored here)"
            )
    return profile


def validate_config(raw: Any, *, source: Path | str = CONFIG_PATH) -> ProxmoxConfig:
    if not isinstance(raw, dict):
        raise CatalogError(f"{source}: config must be a mapping")
    if raw.get("version") != CONFIG_VERSION:
        raise CatalogError(f"{source}: version must be {CONFIG_VERSION}")

    profiles_raw = raw.get("profiles") or {}
    if not isinstance(profiles_raw, dict):
        raise CatalogError(f"{source}: profiles must be a mapping")

    profiles: dict[str, Profile] = {}
    for name, data in profiles_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise CatalogError(f"{source}: invalid profile name {name!r}")
        profiles[name] = validate_profile(name, data, source=source)

    default = raw.get("default") or ""
    if not isinstance(default, str):
        raise CatalogError(f"{source}: default must be a string")
    if default and default not in profiles:
        raise CatalogError(f"{source}: default names an unknown profile: {default!r}")
    if not default and len(profiles) == 1:
        # One profile is unambiguously the default, whether or not it says so.
        default = next(iter(profiles))

    return ProxmoxConfig(profiles=profiles, default=default, target=Path(source))
