"""The pure decision layer for `devstuff configure syncthing`. No I/O.

Everything here operates on plain dicts of the shape `GET /rest/config`
returns, so the whole decision table is testable without a daemon.

Measured against syncthing v2.1.6 — each behaviour below is load-bearing:
- `POST /rest/config/devices` and `POST /rest/config/folders` upsert by id and
  accept any id (slashes included); `PUT …/<id>` 404s on an id containing `/`.
- A folder's `devices` entry naming a device absent from `config.devices` is
  *silently dropped* — the device must be written before the folder, and the
  result re-read (`verify`).
- A folder `type` the daemon does not know is stored as `sendreceive` with a
  200 — the mode comes from a picker and is verified after the write.
- Overlapping or nested folder paths return 200 and sit `idle`. The overlap
  check is ours.
- A relative or `~/` folder path is accepted and resolved against the daemon's
  working directory — the wizard always sends an absolute, resolved path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# The base32 alphabet syncthing device IDs use: no 0, 1, 8 or 9.
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

# A device ID is 52 base32 data chars in 4 chunks of 13, each chunk followed by
# its Luhn mod-32 check char — 56 chars total, printed as 8 groups of 7.
_ID_DATA_LEN = 56

DEFAULT_FOLDER_PATH = "~/SyncthingShared"
DEFAULT_SERVER_NAME = "Storage server"

# The two modes the wizard offers, in picker order.
FOLDER_MODES = {
    "sendreceive": "Send & receive",
    "receiveonly": "Receive only",
}


def _luhn32_check(data: str) -> str:
    """One Luhn mod-32 check char over a 13-char group (syncthing's luhnBase32)."""
    factor = 1
    total = 0
    for ch in data:
        addend = factor * _ALPHABET.index(ch)
        factor = 2 if factor == 1 else 1
        addend = addend // 32 + addend % 32
        total += addend
    return _ALPHABET[(32 - total % 32) % 32]


def normalize_device_id(raw: str) -> str:
    """Uppercase, strip whitespace and dashes, regroup into 8×7 with dashes."""
    compact = re.sub(r"[\s-]+", "", raw).upper()
    return "-".join(compact[i : i + 7] for i in range(0, len(compact), 7))


def device_id_problem(raw: str) -> str | None:
    """A human-readable reason the ID cannot be right, or None if it is valid.

    Local validation catches a typo before anything is written; syncthing itself
    only answers 400 to a bad check digit or wrong length.
    """
    compact = re.sub(r"[\s-]+", "", raw).upper()
    bad = sorted({c for c in compact if c not in _ALPHABET})
    if bad:
        shown = "".join(bad)
        return f"contains invalid characters ({shown}) — a device ID uses A–Z and 2–7 only"
    if len(compact) != _ID_DATA_LEN:
        return f"is {len(compact)} characters — a device ID is 56 (8 groups of 7)"
    for group in range(4):
        data = compact[group * 14 : group * 14 + 13]
        check = compact[group * 14 + 13]
        if _luhn32_check(data) != check:
            return "has a wrong check digit — re-copy it from 'Actions → Show ID'"
    return None


def paths_overlap(a: Path, b: Path) -> bool:
    """Equal, or one inside the other. `/a/sync` vs `/a/sync2` must be False."""
    ra, rb = a.resolve(), b.resolve()
    return ra == rb or ra in rb.parents or rb in ra.parents


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class Request:
    server_id: str
    server_name: str
    folder_id: str
    folder_path: Path
    mode: str


@dataclass(frozen=True)
class Refusal:
    reason: str


@dataclass(frozen=True)
class Plan:
    add_device: bool
    folder_action: str  # "create" | "share" | "none"
    lines: list[str] = field(default_factory=list)


def _folder_devices(folder: dict) -> list[str]:
    return [d.get("deviceID", "") for d in folder.get("devices", [])]


def plan(config: dict, local_id: str, req: Request) -> Plan | Refusal:
    """What the run would change, over the dict `GET /rest/config` returned."""
    if req.server_id == local_id:
        return Refusal("that is this machine's own Device ID — put the server's ID here")

    devices = config.get("devices", [])
    folders = config.get("folders", [])
    existing = next((f for f in folders if f.get("id") == req.folder_id), None)
    wanted_path = _resolve(req.folder_path)

    if existing is not None:
        have_path = _resolve(existing.get("path", ""))
        if have_path != wanted_path:
            return Refusal(
                f"folder '{req.folder_id}' already exists pointing at {have_path} — "
                "edit it in the Syncthing GUI instead"
            )
        if existing.get("type") != req.mode:
            return Refusal(
                f"folder '{req.folder_id}' already exists as "
                f"'{existing.get('type')}', not '{req.mode}' — edit it in the Syncthing GUI instead"
            )
    else:
        for other in folders:
            other_path = _resolve(other.get("path", ""))
            if paths_overlap(wanted_path, other_path):
                return Refusal(
                    f"{wanted_path} overlaps existing folder '{other.get('id')}' at "
                    f"{other_path} — syncthing accepts overlapping folders and then misbehaves"
                )

    add_device = not any(d.get("deviceID") == req.server_id for d in devices)
    if existing is None:
        folder_action = "create"
    elif req.server_id not in _folder_devices(existing):
        folder_action = "share"
    else:
        folder_action = "none"

    lines: list[str] = []
    if add_device:
        lines.append(f"Add device '{req.server_name}' ({req.server_id})")
    else:
        lines.append(f"Device {req.server_id} already exists — its name and settings are left alone")
    if folder_action == "create":
        lines.append(
            f"Create folder '{req.folder_id}' at {wanted_path} "
            f"({FOLDER_MODES.get(req.mode, req.mode)}), shared with both devices"
        )
    elif folder_action == "share":
        lines.append(f"Share existing folder '{req.folder_id}' with '{req.server_name}'")
    else:
        lines.append(f"Folder '{req.folder_id}' is already shared with '{req.server_name}'")
    lines.append("On the server you must still approve this device and the folder — nothing syncs until then")
    return Plan(add_device=add_device, folder_action=folder_action, lines=lines)


def build_device(defaults: dict, req: Request) -> dict:
    """The `GET /rest/config/defaults/device` template filled in for the server."""
    device = dict(defaults)
    device["deviceID"] = req.server_id
    device["name"] = req.server_name
    device["addresses"] = ["dynamic"]
    device["introducer"] = False
    device["autoAcceptFolders"] = False
    return device


def build_folder(template_or_existing: dict, local_id: str, req: Request, action: str) -> dict:
    """The folder body to POST.

    For "create" the argument is the `GET /rest/config/defaults/folder` template
    (which already lists the local device); for "share" it is the existing
    folder, with the server device appended.
    """
    folder = dict(template_or_existing)
    if action == "create":
        folder["id"] = req.folder_id
        folder["label"] = req.folder_id
        folder["path"] = str(_resolve(req.folder_path))
        folder["type"] = req.mode
        folder["paused"] = False
    device_ids = _folder_devices(folder)
    for device_id in (local_id, req.server_id):
        if device_id not in device_ids:
            folder.setdefault("devices", []).append({"deviceID": device_id})
            device_ids.append(device_id)
    return folder


def verify(config: dict, req: Request) -> list[str]:
    """Problems found by re-reading `GET /rest/config` after the writes.

    Each message names the silent behaviour it guards against, because a 200
    from the API is not evidence the config changed the way it was asked to.
    """
    problems: list[str] = []
    devices = config.get("devices", [])
    if not any(d.get("deviceID") == req.server_id for d in devices):
        problems.append(
            f"device {req.server_id} is absent from the config — the API accepted the "
            "write but did not store it"
        )
    folder = next((f for f in config.get("folders", []) if f.get("id") == req.folder_id), None)
    if folder is None:
        problems.append(f"folder '{req.folder_id}' is absent from the config after the write")
        return problems
    have_path = _resolve(folder.get("path", ""))
    if have_path != _resolve(req.folder_path):
        problems.append(
            f"folder '{req.folder_id}' was stored with path {have_path}, "
            f"not {_resolve(req.folder_path)}"
        )
    if folder.get("type") != req.mode:
        problems.append(
            f"folder '{req.folder_id}' was stored as type '{folder.get('type')}' — "
            f"syncthing coerces an unknown type to sendreceive rather than rejecting it "
            f"(asked for '{req.mode}')"
        )
    if req.server_id not in _folder_devices(folder):
        problems.append(
            f"folder '{req.folder_id}' does not list {req.server_id} — syncthing silently "
            "drops a devices entry whose device is not in the config"
        )
    return problems
