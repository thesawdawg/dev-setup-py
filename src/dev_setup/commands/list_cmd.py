from __future__ import annotations

import click
from rich.table import Table

from dev_setup import registry, ui


@click.command("list")
@click.option("--installed", "show_filter", flag_value="installed", help="Show only installed packages")
@click.option("--available", "show_filter", flag_value="available", help="Show only uninstalled packages")
@click.argument("category", required=False)
def list_cmd(show_filter: str, category: str) -> None:
    """List available packages."""
    ui.print_banner()

    tools = registry.all_tools()
    by_cat: dict = {}

    for tool in tools:
        if category and tool.category != category:
            continue
        is_inst = tool.is_installed()
        if show_filter == "installed" and not is_inst:
            continue
        if show_filter == "available" and is_inst:
            continue
        by_cat.setdefault(tool.category, []).append((tool, is_inst))

    if not by_cat:
        ui.warn("No packages match the given filters.")
        return

    for cat in ("core", "tools", "custom"):
        entries = by_cat.get(cat, [])
        if not entries:
            continue

        ui.console.print(f"  [bold magenta]{cat.upper()}[/]")
        ui.divider()

        tbl = Table(box=None, padding=(0, 1), show_header=False)
        tbl.add_column(width=2)
        tbl.add_column(style="bold", min_width=14)
        tbl.add_column(min_width=38)
        tbl.add_column(style="dim", min_width=8)
        tbl.add_column(style="dim")

        for tool, is_inst in entries:
            icon = "[green bold]✔[/]" if is_inst else "[red bold]✘[/]"
            version = tool.get_version() if is_inst else ""
            tbl.add_row(icon, tool.key, tool.description, tool.install_type, version)

        ui.console.print(tbl)
        ui.console.print()
