from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

import click
import questionary
from rich.markup import escape

from dev_setup import registry, ui
from dev_setup.base import Tool
from dev_setup.commands._plan import plan_for, plan_summary


@click.command("remove")
@click.option("-y", "--yes", is_flag=True, help="Skip plan previews and confirmations.")
@click.argument("packages", nargs=-1)
def remove_cmd(packages: tuple[str, ...], yes: bool) -> None:
    """Uninstall installed packages. Interactive picker when called with no arguments."""
    if not packages:
        _remove_interactive(yes=yes)
        return

    tools: list[Tool] = []
    for key in packages:
        tool = registry.get(key)
        if tool is None:
            ui.error(f"Unknown package: '{key}'")
            sys.exit(1)
        tools.append(tool)

    # Report not-installed tools up front so the plan only covers real work.
    results: list[tuple[str, str, str]] = []
    to_remove: list[Tool] = []
    for t in tools:
        if not t.is_installed():
            ui.warn(f"{t.name} is not installed — nothing to remove.")
            results.append((t.key, "skip", "not installed"))
        else:
            to_remove.append(t)

    if not to_remove:
        _report(results)
        return

    if not yes and not _confirm_plans(to_remove):
        ui.warn("Aborted.")
        return

    results += _remove_all(to_remove, yes=yes)
    _report(results)


def _remove_all(tools: list[Tool], *, yes: bool) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    for i, tool in enumerate(tools, 1):
        pos = f"{i}/{len(tools)}" if len(tools) > 1 else ""
        status, detail = _remove_one(tool, yes=yes, position=pos, confirmed=True)
        results.append((tool.key, status, detail))
    return results


def _remove_one(
    tool: Tool, *, yes: bool = False, position: str = "", confirmed: bool = False
) -> tuple[str, str]:
    """Remove one tool. Returns (status, detail)."""
    ui.section(f"Remove {tool.name}", note=position)

    if not tool.is_installed():
        ui.warn(f"{tool.name} is not installed — nothing to remove.")
        return "skip", "not installed"

    deps = registry.dependents(tool.key)
    if deps:
        ui.warn(
            f"{tool.name} is required by {len(deps)} catalog tool(s): "
            f"{', '.join(deps)}"
        )

    if not confirmed and not yes and not _confirm_plan(tool):
        ui.dim("Skipped.")
        return "skip", "skipped"

    try:
        tool.remove()
        ui.success(f"{tool.name} removed")
        return "ok", "removed"
    except Exception as exc:
        ui.error(f"Failed to remove {tool.name}: {escape(str(exc))}")
        return "fail", str(exc).splitlines()[-1][:120]


def _confirm_plan(tool: Tool) -> bool:
    ui.plan_panel(f"Remove {tool.name}", plan_for(tool, "remove"))
    return ui.confirm(f"Remove {tool.name}?", default=False)


def _confirm_plans(tools: list[Tool]) -> bool:
    if len(tools) == 1:
        return _confirm_plan(tools[0])
    rows = [(t.key, plan_summary(t, "remove")) for t in tools]
    ui.batch_plan_panel(rows, "Remove")
    return ui.confirm(f"Remove {len(tools)} packages?", default=False)


def _report(results: list[tuple[str, str, str]]) -> None:
    if len(results) > 1:
        ui.summary(results, title="Remove")
    if any(s == "fail" for _, s, _ in results):
        sys.exit(1)


def _remove_interactive(*, yes: bool) -> None:
    ui.print_banner()
    tools = registry.all_tools()

    with ui.spinner("Checking installed packages..."), ThreadPoolExecutor(max_workers=8) as pool:
        statuses = pool.map(lambda t: t.is_installed(), tools)
        installed = [t for t, flag in zip(tools, statuses, strict=True) if flag]

    if not installed:
        ui.info("No installed packages to remove.")
        return

    key_width = max(len(t.key) for t in installed) + 2
    choices: list = []
    for t in sorted(installed, key=lambda t: t.key):
        deps = registry.dependents(t.key)
        annotation = f"  ⚠ required by: {', '.join(deps)}" if deps else ""
        # Plain-string title: a token list would render verbatim and never pick
        # up the `highlighted` style, leaving the cursor invisible.
        title = f"{t.key:<{key_width}}{t.description}{annotation}"
        choices.append(questionary.Choice(title=title, value=t.key, checked=False))

    selected = ui.checkbox(
        "Select packages to remove:",
        choices=choices,
        instruction="(Space toggle · Enter confirm · required-by entries warn only)",
    )

    if not selected:
        ui.info("No packages selected.")
        return

    tools_to_remove = [t for k in selected if (t := registry.get(k)) is not None]
    if not yes and not _confirm_plans(tools_to_remove):
        ui.warn("Aborted.")
        return

    results = _remove_all(tools_to_remove, yes=yes)
    _report(results)
