"""Gathering a profile from the machine (docs/specs/profile, FR-7, FR-10–13).

`profile.py` is the file format and stays pure; this is the part that asks the machine. It
makes no network call: `is_installed()` and `installed_version()` are local reads (spec NFR-2),
and it never goes through `get_version()`'s free text.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from dev_setup import registry
from dev_setup.base import Tool
from dev_setup.generic import supports_pin
from dev_setup.profile import Entry, Profile, ProfileError

_MAX_WORKERS = 8

# Why a pinnable tool's version couldn't be read, by install type — so the warning says what
# to look at instead of "version unreadable".
_WHY_UNREADABLE = {
    "npm": "npm doesn't list it as a global package",
    "pip": "uv doesn't list it as a tool (is it installed another way?)",
    "uvx": "uv doesn't list it as a tool (is it installed another way?)",
    "apt": "dpkg doesn't report a version for it",
}


@dataclass(frozen=True)
class Snapshot:
    profile: Profile
    # Pinnable tools whose version could not be read, when versions were asked for. They are in
    # `profile` without a version; naming them is what keeps that from looking deliberate.
    unreadable: tuple[str, ...] = ()
    # Installed tools defined or overridden in the user's own catalog. Another machine's
    # catalog won't know these keys.
    custom: tuple[str, ...] = ()
    # key -> reason, for each entry in `unreadable`.
    why: dict[str, str] = field(default_factory=dict)


def _probe_one(tool: Tool, versions: bool) -> tuple[Tool, bool, str | None]:
    """`(tool, installed, version)`; `version` is None unless asked for and pinnable. Never raises."""
    try:
        if not tool.is_installed():
            return tool, False, None
    except Exception:
        # Cannot claim it is installed, so it is left out rather than listed (as `outdated` does).
        return tool, False, None
    if not versions or not supports_pin(tool):  # type: ignore[arg-type]
        return tool, True, None
    reader = getattr(tool, "installed_version", None)
    try:
        return tool, True, (reader() if reader else "")
    except Exception:
        return tool, True, ""


def take(tools: Sequence[Tool] | None = None, *, versions: bool = False) -> Snapshot:
    """Snapshot the installed tools. `versions=True` also records readable, pinnable versions."""
    if tools is None:
        tools = registry.all_tools()
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        probed = list(pool.map(lambda t: _probe_one(t, versions), tools))

    entries: dict[str, Entry] = {}
    unreadable: list[str] = []
    why: dict[str, str] = {}
    custom: list[str] = []
    for tool, installed, version in probed:
        if not installed:
            continue
        entry = Entry()
        if version is not None:
            try:
                entry = Entry(version or None)
            except ProfileError:
                version = ""  # a value the profile format would refuse: treat as unreadable
            if not version:
                unreadable.append(tool.key)
                why[tool.key] = _WHY_UNREADABLE.get(tool.install_type, "no reader for this type")
        entries[tool.key] = entry
        if not getattr(tool, "builtin", True):
            custom.append(tool.key)

    return Snapshot(
        profile=Profile(entries),
        unreadable=tuple(sorted(unreadable)),
        custom=tuple(sorted(custom)),
        why=why,
    )
