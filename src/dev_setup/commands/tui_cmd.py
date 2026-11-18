"""`devstuff tui` — experimental, read-only catalog browser.

Shows the effective catalog (bundled + user) and each tool's *nominal*
install plan. It never probes installed state, never resolves `requires`,
and never executes — the runner and registry's probe paths are not touched.
Heavy imports (registry, the tui renderer) stay inside the function so a
plain `devstuff` startup doesn't pay for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from dev_setup.base import Tool

if TYPE_CHECKING:
    from dev_setup.tui import Entry


def _one_line(value: str) -> str:
    """Collapse catalog text to a safe single line — control bytes, embedded
    newlines and ANSI/format characters become ordinary spacing."""
    return " ".join(
        "".join(char if char.isprintable() else " " for char in value).split()
    )


def _plan_label(kind: str, label: str, detail: str) -> str:
    """One safe display line per plan item. `cmd` details are argv for a
    preview, never execution; `script` bodies are dropped entirely — raw
    shell text is not terminal-safe and not the point of a plan review."""
    if kind == "cmd":
        return _one_line(f"{label}: {detail}" if label else detail)
    if kind == "script":
        return _one_line(f"{label or 'install script'}: script body omitted")
    return _one_line(detail)


def _entry(tool: Tool) -> Entry:
    from dev_setup import tui
    from dev_setup.commands._plan import plan_for

    return tui.Entry(
        key=_one_line(tool.key),
        description=_one_line(getattr(tool, "description", "") or ""),
        category=_one_line(getattr(tool, "category", "") or "custom"),
        source=_one_line(getattr(tool, "install_type", "") or "unknown"),
        plan=tuple(_plan_label(*item) for item in plan_for(tool, "install")),
    )


@click.command("tui")
def tui_cmd() -> None:
    """Browse catalog tools and preview install plans (experimental, read-only)."""
    from dev_setup.commands.configure_cmd import require_prompt

    require_prompt(
        "The TUI needs a terminal. Use devstuff list for non-interactive inventory."
    )
    from dev_setup import registry, tui
    from dev_setup.catalog import CatalogError

    try:
        tools = registry.all_tools()
    except CatalogError as exc:
        raise click.ClickException(str(exc)) from exc
    tui.browse([_entry(t) for t in tools],
               title="devstuff tools (read-only catalog preview)")
