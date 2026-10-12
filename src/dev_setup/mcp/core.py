"""What the MCP server runs and what it will let a client reach (docs/specs/mcp).

Pure: no I/O, no sockets, no processes. Everything here is decided from data, so the whole table
is tested without a server — the reason `profile.py` and `apply_plan.py` are built the same way.

M-1 holds only the classification (FR-1a). Dispatch, the probe lifecycle and the JSON-RPC
handling arrive in M-2 and read the same classified toolbox, so listing and calling cannot
disagree about what exists (FR-3).
"""

from __future__ import annotations

from enum import StrEnum

from dev_setup.agent.registry import AgentTool


class Gate(StrEnum):
    READ_ONLY = "read-only"  # always exposed
    INSTALL = "install"  # only with --allow-install
    FUNCTION = "function"  # only with --allow-functions
    UNCLASSIFIED = "unclassified"  # never: an unknown catalog bridge fails closed
    PRIMITIVE = "primitive"  # never: workspace tools do not exist on this surface (FR-1)


# One row per bridge in `agent.bridges._BRIDGES`, keyed by *target* — what the entry runs.
# `test_every_bridge_has_a_gate` fails when a bridge is added without a row, so a new bridge
# cannot ship without someone deciding which flag, if any, guards it.
CATALOG_GATES: dict[str, Gate] = {
    "list_tools": Gate.READ_ONLY,
    "search_catalog": Gate.READ_ONLY,
    "tool_info": Gate.READ_ONLY,
    "outdated": Gate.READ_ONLY,
    "profile_diff": Gate.READ_ONLY,
    "install_tool": Gate.INSTALL,
}


def classify(tool: AgentTool) -> Gate:
    """The gate for one agent-toolbox entry, from `(impl, target)` and nothing else.

    Never from the key and never from `tool.mutating` alone: both are chosen by the user's
    `agent_tools.yaml`, which can rename `install_tool`, alias it, or label it harmless (F-10a).
    `mutating: true` can only move a read-only entry *into* a gate, never out of one.
    """
    if tool.impl == "primitive":
        return Gate.PRIMITIVE
    if tool.impl == "function":
        return Gate.FUNCTION
    if tool.impl == "catalog":
        gate = CATALOG_GATES.get(tool.target or "", Gate.UNCLASSIFIED)
        if gate is Gate.READ_ONLY and tool.mutating:
            return Gate.FUNCTION
        return gate
    return Gate.UNCLASSIFIED


def is_exposed(gate: Gate, *, allow_install: bool, allow_functions: bool) -> bool:
    """Whether a tool of this class exists for a server started with these flags (FR-3)."""
    if gate is Gate.READ_ONLY:
        return True
    if gate is Gate.INSTALL:
        return allow_install
    if gate is Gate.FUNCTION:
        return allow_functions
    return False
