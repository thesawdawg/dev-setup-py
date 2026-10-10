"""A Proxmox that fits in a file.

There is no PVE host to test against, so the tests put one on `PATH` instead: stub `ssh`
and `curl` binaries that answer the same API paths a real node would, record what they
were asked for, and let every `pve-*` function run end to end.

What the stubs prove is what devstuff *sends* — the resolution, the qm/pct choice, the
confirmation gate, the exit codes. What Proxmox does with it is out of their reach, which
is what `devstuff run pve-check` is for.
"""

from __future__ import annotations

import json
import os
import re

# The two transports really do differ here: `curl` against /api2/json returns
# {"data": ...} and `pvesh get --output-format json` unwraps it. Both stubs go through
# the same router so fmt.py's handling of both is exercised.
GUESTS = [
    {"vmid": 101, "type": "qemu", "name": "web01", "node": "pve1", "status": "running",
     "uptime": 93600, "cpu": 0.05, "mem": 2147483648, "maxmem": 4294967296,
     "maxdisk": 34359738368, "tags": "prod"},
    {"vmid": 102, "type": "qemu", "name": "db01", "node": "pve2", "status": "stopped",
     "maxmem": 8589934592, "maxdisk": 107374182400},
    {"vmid": 200, "type": "lxc", "name": "dns", "node": "pve2", "status": "running",
     "uptime": 500000, "cpu": 0.01, "mem": 134217728, "maxmem": 536870912,
     "maxdisk": 8589934592},
    {"vmid": 103, "type": "qemu", "name": "build01", "node": "pve1", "status": "stopped",
     "maxmem": 4294967296, "maxdisk": 34359738368},
    {"vmid": 201, "type": "lxc", "name": "mail", "node": "pve1", "status": "stopped",
     "maxmem": 1073741824, "maxdisk": 8589934592},
    {"vmid": 300, "type": "qemu", "name": "twin", "node": "pve1", "status": "running",
     "maxmem": 1073741824, "maxdisk": 8589934592},
    {"vmid": 301, "type": "lxc", "name": "twin", "node": "pve2", "status": "running",
     "maxmem": 1073741824, "maxdisk": 8589934592},
    {"vmid": 400, "type": "qemu", "name": "locked-vm", "node": "pve1", "status": "stopped",
     "lock": "backup", "maxmem": 1073741824, "maxdisk": 8589934592},
]

NODES = [
    {"node": "pve1", "status": "online", "uptime": 864000, "cpu": 0.12, "maxcpu": 8,
     "mem": 8589934592, "maxmem": 34359738368},
    {"node": "pve2", "status": "online", "uptime": 432000, "cpu": 0.30, "maxcpu": 4,
     "mem": 4294967296, "maxmem": 17179869184},
]

CLUSTER = [
    {"type": "cluster", "name": "lab", "quorate": 1, "nodes": 2, "version": 4},
    {"type": "node", "name": "pve1", "online": 1, "local": 1, "ip": "10.0.0.11", "nodeid": 1},
    {"type": "node", "name": "pve2", "online": 1, "local": 0, "ip": "10.0.0.12", "nodeid": 2},
]

VERSION = {"version": "8.2.4", "release": "8.2", "repoid": "faa83925c9641325", "console": "html5"}

STORAGE = {
    "pve1": [
        {"storage": "local", "type": "dir", "content": "backup,iso,vztmpl", "active": 1,
         "enabled": 1, "shared": 0, "total": 107374182400, "used": 53687091200,
         "avail": 53687091200, "used_fraction": 0.5},
        {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir", "active": 1,
         "enabled": 1, "shared": 0, "total": 214748364800, "used": 193273528320,
         "avail": 21474836480, "used_fraction": 0.9},
        {"storage": "nas", "type": "pbs", "content": "backup", "active": 1, "enabled": 1,
         "shared": 1, "total": 2199023255552, "used": 439804651110,
         "avail": 1759218604442, "used_fraction": 0.2},
    ],
    "pve2": [
        {"storage": "local", "type": "dir", "content": "iso,vztmpl", "active": 1, "enabled": 1,
         "shared": 0, "total": 107374182400, "used": 10737418240, "avail": 96636764160,
         "used_fraction": 0.1},
        {"storage": "nas", "type": "pbs", "content": "backup", "active": 1, "enabled": 1,
         "shared": 1, "total": 2199023255552, "used": 439804651110,
         "avail": 1759218604442, "used_fraction": 0.2},
    ],
}

SNAPSHOTS = {
    "101": [
        {"name": "pre-upgrade", "snaptime": 1714000000, "vmstate": 0, "parent": "",
         "description": "before the 8.2 upgrade"},
        {"name": "nightly", "snaptime": 1715000000, "vmstate": 1, "parent": "pre-upgrade",
         "description": ""},
        {"name": "current", "digest": "abc", "parent": "nightly", "description": "You are here!"},
    ],
    # A guest with no snapshots at all: "current" alone. Printing "none" here is a
    # success, not a failure (FR-21).
    "200": [{"name": "current", "digest": "def", "description": "You are here!"}],
}

BACKUPS = [
    {"volid": "local:backup/vzdump-qemu-101-2026_09_14-02_00_03.vma.zst", "vmid": 101,
     "ctime": 1757808003, "size": 4294967296, "format": "vma.zst", "protected": 0},
    {"volid": "local:backup/vzdump-lxc-200-2026_09_14-02_10_01.tar.zst", "vmid": 200,
     "ctime": 1757808601, "size": 1073741824, "format": "tar.zst", "protected": 1},
]

UPDATES = {
    "pve1": [],
    "pve2": [
        {"Package": "pve-manager", "OldVersion": "8.2.2", "Version": "8.2.4",
         "Origin": "Proxmox", "Title": "Proxmox VE management tools", "Priority": "optional"},
    ],
}

SUBSCRIPTION = {"status": "notfound", "message": "There is no subscription key",
                "serverid": "ABC", "url": "https://www.proxmox.com/"}


def route(path: str, query: dict[str, str]) -> tuple[object, int]:
    """(payload, http-ish status) for an API path. 403 where a real node would say so."""
    if os.environ.get("PVE_STUB_FORBID") and path.startswith(os.environ["PVE_STUB_FORBID"]):
        return {"errors": "Permission check failed"}, 403

    if path == "/version":
        return VERSION, 200
    if path == "/cluster/resources":
        return GUESTS, 200
    if path == "/cluster/status":
        return CLUSTER, 200
    if path == "/nodes":
        return NODES, 200

    match = re.fullmatch(r"/nodes/([^/]+)/storage", path)
    if match:
        pools = STORAGE.get(match.group(1), [])
        wanted = query.get("content")
        if wanted:
            pools = [p for p in pools if wanted in str(p.get("content", "")).split(",")]
        return pools, 200

    match = re.fullmatch(r"/nodes/([^/]+)/storage/([^/]+)/content", path)
    if match:
        return BACKUPS, 200

    match = re.fullmatch(r"/nodes/([^/]+)/(qemu|lxc)/(\d+)/snapshot", path)
    if match:
        return SNAPSHOTS.get(match.group(3), []), 200

    match = re.fullmatch(r"/nodes/([^/]+)/apt/update", path)
    if match:
        return UPDATES.get(match.group(1), []), 200

    match = re.fullmatch(r"/nodes/([^/]+)/subscription", path)
    if match:
        return SUBSCRIPTION, 200

    return {"errors": f"no such path: {path}"}, 501


def record(kind: str, detail: str) -> None:
    log = os.environ.get("PVE_STUB_LOG")
    if not log:
        return
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(f"{kind}\t{detail}\n")


def dumps(payload: object, *, envelope: bool) -> str:
    return json.dumps({"data": payload} if envelope else payload)
