"""Probing installed tools for newer versions — the one place `update` and `outdated` share.

Kept free of UI and prompt code so it can be exercised without a terminal, and so
`outdated --json` can call it without risk of writing anything to stdout.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum

from dev_setup import registry
from dev_setup.base import Tool
from dev_setup.generic import UpdateStatus, supports_update_check

_MAX_WORKERS = 8


def _probe_one(tool: Tool) -> tuple[Tool, UpdateStatus] | None:
    """`(tool, status)` if the tool is installed, else None. Never raises.

    One tool's probe failing must not take the whole run down: a checker that raises
    becomes an empty status ("couldn't tell"), which is the same answer a checker that
    catches its own error already gives. A tool whose `is_installed()` raises is treated
    as not installed — we cannot claim it is, and an `unknown` row for it would invite
    an update of something that may not be there.
    """
    try:
        if not tool.is_installed():
            return None
    except Exception:
        return None
    # `check_for_update` lives on GenericTool, not the Tool ABC: it is not part of the
    # contract every tool implementation must meet, so absence means "can't tell".
    check = getattr(tool, "check_for_update", None)
    if check is None:
        return tool, UpdateStatus()
    try:
        return tool, check()
    except Exception:
        return tool, UpdateStatus()


def collect_candidates(
    tools: Sequence[Tool] | None = None,
) -> list[tuple[Tool, UpdateStatus]]:
    """Return `(tool, UpdateStatus)` for every *installed* tool, probed concurrently.

    `tools` defaults to the whole registry; pass a subset to check only those. Order
    follows the input. Uninstalled tools are omitted — callers that need to report them
    compare against what they asked for.
    """
    if tools is None:
        tools = registry.all_tools()
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        probed = list(pool.map(_probe_one, tools))
    return [p for p in probed if p is not None]


# -- Classification ------------------------------------------------------------------


class State(StrEnum):
    """What we can honestly say about one tool. Values are the `--json` spellings."""

    OUTDATED = "outdated"
    CURRENT = "current"
    # A checker exists for this install type but couldn't answer (offline, tool not
    # managed by that mechanism, probe error).
    UNKNOWN = "unknown"
    # No checker exists for this install type — script/bash installers. Nothing the user
    # does to their network will change this.
    UNSUPPORTED = "unsupported"
    NOT_INSTALLED = "not-installed"


def classify(install_type: str, status: UpdateStatus | None, *, installed: bool = True) -> State:
    """Map a tool's install type and probe result to exactly one `State`.

    `unknown` and `unsupported` are never `current`: "could not check" must not read as
    "up to date". They are told apart by *install type*, not by the status — a checker
    that fails and a type with no checker return the same empty `UpdateStatus`, so
    the status alone cannot distinguish them (spec SD-2).
    """
    if not installed:
        return State.NOT_INSTALLED
    if not supports_update_check(install_type):
        return State.UNSUPPORTED
    if status is None or status.available is None:
        return State.UNKNOWN
    return State.OUTDATED if status.available else State.CURRENT


@dataclass(frozen=True)
class Row:
    """One tool's answer, in the shape `outdated` renders and `--json` emits."""

    key: str
    type: str
    state: State
    installed: str | None
    latest: str | None
    # Why a row is `unknown`, when a checker knows (UpdateStatus.note); otherwise empty.
    note: str = ""

    def to_json(self) -> dict[str, str | None]:
        return {
            "key": self.key,
            "type": self.type,
            "state": self.state.value,
            "installed": self.installed,
            "latest": self.latest,
            "note": self.note,
        }


def make_row(tool: Tool, status: UpdateStatus | None, *, installed: bool = True) -> Row:
    install_type = tool.install_type
    state = classify(install_type, status, installed=installed)
    if state is State.NOT_INSTALLED or status is None:
        return Row(tool.key, install_type, state, None, None)
    return Row(
        tool.key, install_type, state, status.current or None, status.latest or None, status.note
    )


# Display order (FR-9). `unsupported` goes last: it is the long, low-information tail.
_ORDER = {
    State.OUTDATED: 0,
    State.UNKNOWN: 1,
    State.CURRENT: 2,
    State.NOT_INSTALLED: 3,
    State.UNSUPPORTED: 4,
}


def sort_rows(rows: Iterable[Row]) -> list[Row]:
    return sorted(rows, key=lambda r: (_ORDER[r.state], r.key))


def summarize(rows: Iterable[Row]) -> dict[State, int]:
    """Count rows per state. Every state is present, so the counts always sum to the rows."""
    counts = dict.fromkeys(State, 0)
    for r in rows:
        counts[r.state] += 1
    return counts
