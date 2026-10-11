"""Shared plan-preview helpers for install/remove/update commands."""

from __future__ import annotations

from dev_setup.base import Tool


def plan_for(tool: Tool, action: str, version: str | None = None) -> list:
    """The tool's resolved plan items, or a generic note for non-GenericTool entries."""
    planner = getattr(tool, "plan_items", None)
    if planner is None:
        return [("note", "", f"{action} {tool.key}")]
    return planner(action, version)


def plan_summary(tool: Tool, action: str, version: str | None = None) -> str:
    """One-line digest of a tool's plan for the batch preview table."""
    parts = []
    for kind, _label, detail in plan_for(tool, action, version):
        if kind == "cmd":
            parts.append(detail)
        elif kind == "script":
            parts.append("runs a script")
        else:
            parts.append(detail)
    return ";  ".join(parts)
