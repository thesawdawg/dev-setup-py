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

    _ORDER = {"core": 0, "tools": 1, "custom": 999}
    for cat in sorted(by_cat, key=lambda c: (_ORDER.get(c, 500), c)):
        entries = by_cat.get(cat, [])
        if not entries:
            continue

        ui.console.print(f"  [bold magenta]{cat.upper()}[/]")
        ui.divider()

        tbl = Table(box=None, padding=(0, 1), show_header=True, header_style="dim")
        tbl.add_column("", width=2)
        tbl.add_column("Package", style="bold", min_width=12)
        tbl.add_column("Description", min_width=36)
        tbl.add_column("Type", style="dim", min_width=8)
        tbl.add_column("Version", style="dim")

        for tool, is_inst in entries:
            icon = "[green bold]✔[/]" if is_inst else "[red bold]✘[/]"
            version = tool.get_version() if is_inst else ""
            tbl.add_row(icon, tool.key, tool.description, tool.install_type, version)
            if tool.help_cmd:
                tbl.add_row("", "", f"[dim cyan]  ? {tool.help_cmd}[/]", "", "")

        ui.console.print(tbl)
        ui.console.print()
