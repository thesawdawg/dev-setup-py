from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import click

from dev_setup import registry, ui
from dev_setup.base import Tool

_MAX_WORKERS = 8


def _probe(tool: Tool) -> tuple[Tool, bool, str]:
    """Gather subprocess-heavy status for one tool (runs in a worker thread)."""
    is_inst = tool.is_installed()
    version = tool.get_version() if is_inst else ""
    return tool, is_inst, version


@click.command("list")
@click.option("--installed", "show_filter", flag_value="installed", help="Show only installed packages")
@click.option("--available", "show_filter", flag_value="available", help="Show only uninstalled packages")
@click.argument("category", required=False)
def list_cmd(show_filter: str, category: str) -> None:
    """List available packages."""
    ui.print_banner()

    tools = registry.all_tools()
    if category:
        tools = [t for t in tools if t.category == category]

    # Probe installed status + version concurrently — each check shells out,
    # so this dominates the command's runtime when done serially.
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        probed = list(pool.map(_probe, tools))

    by_cat: dict = {}
    for tool, is_inst, version in probed:
        if show_filter == "installed" and not is_inst:
            continue
        if show_filter == "available" and is_inst:
            continue
        by_cat.setdefault(tool.category, []).append((tool, is_inst, version))

    if not by_cat:
        ui.warn("No packages match the given filters.")
        return

    _ORDER = {"core": 0, "tools": 1, "custom": 999}
    for cat in sorted(by_cat, key=lambda c: (_ORDER.get(c, 500), c)):
        entries = sorted(by_cat.get(cat, []), key=lambda e: e[0].key)
        if not entries:
            continue

        n_inst = sum(1 for _, is_inst, _ in entries if is_inst)
        tbl = ui.table(
            title=f"[bold]{cat}[/]  [{ui.GRAY}]{n_inst}/{len(entries)} installed[/]"
        )
        tbl.add_column("", width=2, justify="center")
        tbl.add_column("Package", style="bold", no_wrap=True)
        tbl.add_column("Description", ratio=1)
        tbl.add_column("Type", style=ui.CYAN, no_wrap=True)
        tbl.add_column("Version", style=ui.GRAY, no_wrap=True, max_width=28, overflow="ellipsis")

        for tool, is_inst, version in entries:
            icon = f"[{ui.GREEN}]●[/]" if is_inst else f"[{ui.GRAY}]○[/]"
            desc = tool.description
            if tool.help_cmd:
                desc += f"\n[{ui.GRAY}]  ↳ {tool.help_cmd}[/]"
            if not is_inst:
                missing = registry.missing_requires(tool)
                if missing:
                    desc += f"\n[{ui.AMBER}]  ⚠ requires: {', '.join(missing)}[/]"
            tbl.add_row(icon, tool.key, desc, tool.install_type, version)

        ui.console.print(tbl)
        ui.console.print()
