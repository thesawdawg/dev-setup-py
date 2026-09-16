#!/usr/bin/env python3
"""Render Proxmox API JSON as text tables. Reads JSON on stdin, writes a view to stdout.

Run by `lib.sh` as `"$PVE_PYTHON" "$PVE_FMT" <view> [args]`, where `PVE_PYTHON` is the
interpreter devstuff is already running under — so this is guaranteed to exist and to be
at least 3.11, and the functions need neither `jq` nor `yq` (SD-5).

**Standalone on purpose**: stdlib only, and no `dev_setup` import. It is invoked as a
script by a shell function, and a module that only works when the package around it is
importable would be a strange thing to hand a subprocess.

**The two transports hand it slightly different bytes.** `curl` against `/api2/json/...`
returns `{"data": ...}`; `pvesh get --output-format json` unwraps it and returns the
payload directly. `_payload()` accepts either, which is what lets one set of views serve
both transports.
"""

from __future__ import annotations

import difflib
import json
import sys
from typing import Any

EXIT_BAD_INPUT = 1
EXIT_NOT_RESOLVED = 2


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def _payload(text: str | None = None) -> Any:
    raw = sys.stdin.read() if text is None else text
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Proxmox returned something that is not JSON: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_BAD_INPUT) from exc
    if isinstance(data, dict) and set(data) <= {"data", "success", "errors"} and "data" in data:
        return data["data"]
    return data


def _rows(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def human_bytes(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "0"
    for unit in ("B", "K", "M", "G", "T", "P"):
        if n < 1024 or unit == "P":
            return f"{n:.0f}{unit}" if unit in ("B", "K") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def human_duration(seconds: Any) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "-"
    if s <= 0:
        return "-"
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes = s // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def human_time(stamp: Any) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(int(stamp)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "-"


def table(headers: list[str], rows: list[list[str]], *, indent: str = "  ") -> str:
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    # The last column is never padded, so a trailing empty cell adds no trailing spaces.
    def line(cells: list[str]) -> str:
        parts = [c.ljust(widths[i]) if i < len(cells) - 1 else c for i, c in enumerate(cells)]
        return (indent + "  ".join(parts)).rstrip()

    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in rows])


def emit(headers: list[str], rows: list[list[str]], empty: str) -> int:
    """Print a table, or say there was nothing — which is an answer, not a failure, so
    it still exits 0 (FR-21)."""
    if not rows:
        print(f"  {empty}")
        return 0
    print(table(headers, rows))
    return 0


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def view_version(_args: list[str]) -> int:
    data = _payload()
    if isinstance(data, dict):
        version = data.get("version") or "?"
        release = data.get("release") or ""
        repo = data.get("repoid") or ""
        print(f"Proxmox VE {version}" + (f" (release {release}, {repo})" if release else ""))
    return 0


def view_first_node(_args: list[str]) -> int:
    for row in _rows(_payload()):
        if row.get("node"):
            print(row["node"])
            return 0
    return EXIT_NOT_RESOLVED


def view_node_names(_args: list[str]) -> int:
    for row in sorted(_rows(_payload()), key=lambda r: str(r.get("node", ""))):
        if row.get("node"):
            print(row["node"])
    return 0


def _guest_rows(data: Any) -> list[dict[str, Any]]:
    guests = [r for r in _rows(data) if r.get("type") in ("qemu", "lxc")]
    return sorted(guests, key=lambda r: (str(r.get("node", "")), int(r.get("vmid") or 0)))


def _matches(row: dict[str, Any], needle: str) -> bool:
    haystack = " ".join(
        str(row.get(key, "")) for key in ("vmid", "name", "node", "status", "tags", "pool", "type")
    )
    return needle.lower() in haystack.lower()


def view_guests(args: list[str]) -> int:
    needle = args[0] if args else ""
    rows = _guest_rows(_payload())
    if needle:
        rows = [r for r in rows if _matches(r, needle)]
    out = []
    for r in rows:
        status = str(r.get("status", "?"))
        if r.get("template"):
            status = "template"
        if r.get("lock"):
            status = f"{status} (locked: {r['lock']})"
        out.append([
            str(r.get("vmid", "?")),
            "VM " if r.get("type") == "qemu" else "CT ",
            str(r.get("name") or "-"),
            str(r.get("node") or "-"),
            status,
            human_duration(r.get("uptime")),
            percent(r.get("cpu")),
            f"{human_bytes(r.get('mem'))}/{human_bytes(r.get('maxmem'))}",
            human_bytes(r.get("maxdisk")),
            str(r.get("tags") or ""),
        ])
    empty = f"No guest matches {needle!r}." if needle else "This Proxmox has no guests."
    return emit(
        ["VMID", "KIND", "NAME", "NODE", "STATE", "UPTIME", "CPU", "MEM", "DISK", "TAGS"],
        out,
        empty,
    )


def view_resolve(args: list[str]) -> int:
    """Name or VMID in, one tab-separated record out. Never guesses (SD-9)."""
    if not args or not args[0]:
        print("No guest was named.", file=sys.stderr)
        return EXIT_NOT_RESOLVED
    needle = args[0]
    guests = _guest_rows(_payload())

    if needle.isdigit():
        found = [g for g in guests if str(g.get("vmid")) == needle]
    else:
        found = [g for g in guests if str(g.get("name", "")) == needle]
        if not found:
            found = [g for g in guests if str(g.get("name", "")).lower() == needle.lower()]

    if not found:
        print(f"No guest called {needle!r}.", file=sys.stderr)
        # Substring matching alone misses the case this is actually for: `web02` for
        # `web01` contains neither the other. difflib catches the transposition.
        names = [str(g.get("name", "")) for g in guests if g.get("name")]
        near = difflib.get_close_matches(needle, names, n=5, cutoff=0.6)
        near += [n for n in names if needle.lower() in n.lower() and n not in near]
        for name in near[:5]:
            g = next(g for g in guests if g.get("name") == name)
            print(f"  did you mean {name} ({g.get('vmid')} on {g.get('node')})?", file=sys.stderr)
        if not near:
            print("  List them with:  devstuff run pve-guests", file=sys.stderr)
        return EXIT_NOT_RESOLVED

    if len(found) > 1:
        # Proxmox does not enforce unique names, and picking one would run a state
        # change against the loser of a coin flip.
        print(f"{needle!r} matches {len(found)} guests — name the VMID instead:", file=sys.stderr)
        for g in found:
            print(f"  {g.get('vmid')}  {g.get('name')}  ({g.get('type')} on {g.get('node')})",
                  file=sys.stderr)
        return EXIT_NOT_RESOLVED

    g = found[0]
    print("\t".join([
        str(g.get("vmid", "")),
        str(g.get("type", "")),
        str(g.get("node", "")),
        str(g.get("name") or ""),
        str(g.get("status") or ""),
        str(g.get("lock") or ""),
    ]))
    return 0


def view_cluster(_args: list[str]) -> int:
    rows = _rows(_payload())
    cluster = next((r for r in rows if r.get("type") == "cluster"), None)
    nodes = [r for r in rows if r.get("type") == "node"]
    if cluster:
        quorate = "yes" if cluster.get("quorate") else "NO — the cluster has lost quorum"
        print(f"  Cluster {cluster.get('name', '?')}: {len(nodes)} nodes, quorate: {quorate}")
    elif nodes:
        print("  Standalone node (not part of a cluster).")
    out = [
        [
            str(n.get("name") or "-"),
            "online" if n.get("online") else "OFFLINE",
            "this one" if n.get("local") else "",
            str(n.get("ip") or ""),
        ]
        for n in sorted(nodes, key=lambda r: str(r.get("name", "")))
    ]
    return emit(["NODE", "STATE", "", "IP"], out, "No cluster members reported.")


def view_nodes(_args: list[str]) -> int:
    out = []
    for n in sorted(_rows(_payload()), key=lambda r: str(r.get("node", ""))):
        out.append([
            str(n.get("node") or "-"),
            str(n.get("status") or "?"),
            human_duration(n.get("uptime")),
            percent(n.get("cpu")),
            f"{n.get('maxcpu', '?')} cpu",
            f"{human_bytes(n.get('mem'))}/{human_bytes(n.get('maxmem'))}",
        ])
    return emit(["NODE", "STATE", "UPTIME", "CPU", "CORES", "MEM"], out, "No nodes reported.")


def view_storage(args: list[str]) -> int:
    node = args[0] if args else ""
    out = []
    for s in sorted(_rows(_payload()), key=lambda r: str(r.get("storage", ""))):
        state = "active" if s.get("active") else ("enabled" if s.get("enabled") else "disabled")
        full = s.get("used_fraction")
        warn = ""
        try:
            if full is not None and float(full) >= 0.85:
                warn = "  <- nearly full"
        except (TypeError, ValueError):
            pass
        out.append([
            (f"{node}/" if node else "") + str(s.get("storage") or "-"),
            str(s.get("type") or "-"),
            state,
            percent(full),
            f"{human_bytes(s.get('used'))}/{human_bytes(s.get('total'))}",
            str(s.get("content") or "") + warn,
        ])
    return emit(["STORAGE", "TYPE", "STATE", "USED", "SIZE", "CONTENT"], out,
                "No storage is configured on this node.")


def view_updates(_args: list[str]) -> int:
    out = []
    for p in sorted(_rows(_payload()), key=lambda r: str(r.get("Package", ""))):
        out.append([
            str(p.get("Package") or "-"),
            str(p.get("OldVersion") or "-"),
            str(p.get("Version") or "-"),
            str(p.get("Origin") or ""),
        ])
    return emit(["PACKAGE", "INSTALLED", "AVAILABLE", "ORIGIN"], out,
                "No updates are pending on this node.")


def view_subscription(_args: list[str]) -> int:
    data = _payload()
    if not isinstance(data, dict):
        return 0
    status = str(data.get("status") or "unknown")
    message = str(data.get("message") or "")
    level = str(data.get("level") or "")
    print(f"  Subscription: {status}" + (f" ({level})" if level else ""))
    if message and status.lower() not in ("active",):
        print(f"    {message}")
        print("    Without one, the enterprise repository returns 401 and updates fail;")
        print("    the no-subscription repository is the usual answer for a home lab.")
    return 0


def view_snapshots(_args: list[str]) -> int:
    rows = _rows(_payload())
    # Proxmox reports a pseudo-snapshot named "current" for the live state. It marks
    # where you are in the chain, not something you can roll back to.
    snaps = [s for s in rows if s.get("name") != "current"]
    snaps.sort(key=lambda s: int(s.get("snaptime") or 0))
    out = [
        [
            str(s.get("name") or "-"),
            human_time(s.get("snaptime")),
            "yes" if s.get("vmstate") else "no",
            str(s.get("parent") or ""),
            str(s.get("description") or "").replace("\n", " ").strip(),
        ]
        for s in snaps
    ]
    return emit(["NAME", "TAKEN", "RAM", "PARENT", "DESCRIPTION"], out,
                "This guest has no snapshots.")


def view_snapshot_names(_args: list[str]) -> int:
    """One real snapshot name per line — what `pve-rollback` checks a name against.

    Separate from `snapshots` because grepping a rendered table for a name would break
    the moment a column width changed.
    """
    for s in _rows(_payload()):
        if s.get("name") and s.get("name") != "current":
            print(s["name"])
    return 0


def view_backups(args: list[str]) -> int:
    vmid = args[0] if args else ""
    rows = _rows(_payload())
    if vmid:
        rows = [r for r in rows if str(r.get("vmid", "")) == str(vmid)]
    rows.sort(key=lambda r: int(r.get("ctime") or 0), reverse=True)
    out = [
        [
            human_time(r.get("ctime")),
            str(r.get("vmid") or "-"),
            human_bytes(r.get("size")),
            "protected" if r.get("protected") else "",
            str(r.get("volid") or "-"),
        ]
        for r in rows
    ]
    return emit(["TAKEN", "VMID", "SIZE", "", "VOLUME"], out, "No backups on this storage.")


def view_backup_storages(_args: list[str]) -> int:
    """Storage IDs on this node whose content list includes backups — one per line."""
    for s in _rows(_payload()):
        content = str(s.get("content") or "")
        if "backup" in content.split(",") and s.get("storage"):
            print(s["storage"])
    return 0


VIEWS = {
    "version": view_version,
    "first-node": view_first_node,
    "node-names": view_node_names,
    "guests": view_guests,
    "resolve": view_resolve,
    "cluster": view_cluster,
    "nodes": view_nodes,
    "storage": view_storage,
    "updates": view_updates,
    "subscription": view_subscription,
    "snapshots": view_snapshots,
    "snapshot-names": view_snapshot_names,
    "backups": view_backups,
    "backup-storages": view_backup_storages,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in VIEWS:
        print(f"usage: fmt.py <{'|'.join(VIEWS)}> [args]", file=sys.stderr)
        return EXIT_BAD_INPUT
    return VIEWS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
