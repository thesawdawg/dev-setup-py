"""Probing installed tools for newer versions — the one place `update` and `outdated` share.

Kept free of UI and prompt code so it can be exercised without a terminal, and so
`outdated --json` can call it without risk of writing anything to stdout.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from dev_setup import registry
from dev_setup.base import Tool
from dev_setup.generic import UpdateStatus

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
