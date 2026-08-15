from __future__ import annotations

import json as jsonlib

import click
from rich.table import Table

from dev_setup import platforms, registry, ui


def build_report() -> platforms.PlatformReport:
    """Detected platform plus which catalog entries it can and cannot install.

    Pure data — no printing — so `doctor` and `--json` can share it with the table.
    """
    p = platforms.current()
    report = platforms.PlatformReport(platform=p)
    for tool in registry.all_tools():
        if tool.supported:
            report.supported.append(tool.key)
        elif tool.unsupported_inferred:
            report.incompatible_source.append((tool.key, tool.unsupported_reason))
        else:
            report.unsupported.append((tool.key, tool.unsupported_reason))
    return report


@click.command("platform")
@click.option("--json", "as_json", is_flag=True, help="Emit the detected platform as JSON.")
def platform_cmd(as_json: bool) -> None:
    """Show the detected host platform and what it can install."""
    report = build_report()
    p = report.platform
    pm = p.package_manager

    if as_json:
        click.echo(jsonlib.dumps({
            "id": p.id,
            "name": p.name,
            "family": p.family,
            "package_manager": pm.id if pm else None,
            "package_manager_binary": pm.binary if pm else None,
            "needs_sudo": p.needs_escalation,
            "prefix": str(p.prefix),
            "bin_dir": str(p.bin_dir),
            "traits": sorted(p.traits),
            "detected_from": p.detected_from,
            "forced": report.forced,
            "supported": report.supported,
            "unsupported": [{"key": k, "reason": r} for k, r in report.unsupported],
            "incompatible_source": [
                {"key": k, "reason": r} for k, r in report.incompatible_source
            ],
        }, indent=2))
        return

    ui.section("Platform")

    tbl = Table(box=None, padding=(0, 2), show_header=False)
    tbl.add_column(style="dim", min_width=14)
    tbl.add_column()
    tbl.add_row("Detected", p.name)
    tbl.add_row("Id / family", f"{p.id} / {p.family}")
    if pm:
        sudo = "via sudo" if p.needs_escalation else "no sudo needed"
        found = "" if pm.available() else "  [yellow](not on PATH)[/]"
        tbl.add_row("Packages", f"{pm.id} — `{pm.binary}` ({sudo}){found}")
    else:
        tbl.add_row("Packages", "[yellow]none detected[/]")
    tbl.add_row("Prefix", f"{p.prefix}  [dim](bin: {p.bin_dir})[/]")
    present = ", ".join(sorted(p.traits)) or "none"
    absent = ", ".join(sorted(set(platforms.TRAITS) - p.traits)) or "none"
    tbl.add_row("Provides", present)
    tbl.add_row("Lacks", f"[dim]{absent}[/]")
    tbl.add_row("Detected via", p.detected_from)
    ui.console.print(tbl)
    ui.console.print()

    if report.forced:
        ui.warn(
            "DEVSTUFF_PLATFORM is set — this is a preview of another host's view of "
            "the catalog, not what this machine actually is."
        )
        ui.console.print()

    if not report.unsupported and not report.incompatible_source:
        ui.success(f"All {report.total} catalog packages are available here.")
        return

    ui.info(
        f"{len(report.supported)} of {report.total} catalog packages are available here."
    )

    if report.unsupported:
        ui.console.print()
        ui.console.print("  [bold]Unavailable on this platform[/]")
        ui.divider()
        for key, reason in sorted(report.unsupported):
            tool = registry.get(key)
            ui.console.print(f"  [red bold]✘[/] [bold]{key}[/]  [dim]{reason}[/]")
            if tool is not None and tool.alternative:
                ui.console.print(f"      [cyan]→ use '{tool.alternative}' instead[/]")

    if report.incompatible_source:
        ui.console.print()
        ui.console.print("  [bold]Install source incompatible with this platform[/]")
        ui.divider()
        ui.dim(
            "Read from each package's install source rather than declared in its "
            "definition — override with `devstuff install --force <key>`."
        )
        for key, reason in sorted(report.incompatible_source):
            ui.console.print(f"  [yellow bold]![/] [bold]{key}[/]  [dim]{reason}[/]")
    ui.console.print()
