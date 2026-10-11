from __future__ import annotations

import sys

import click
import questionary
from rich.markup import escape

from dev_setup import registry, ui
from dev_setup.base import Tool
from dev_setup.commands._plan import plan_for, plan_summary
from dev_setup.updates import collect_candidates


@click.command("update")
@click.option(
    "--version", "target_version", default=None,
    help="Update to a specific version instead of latest. Only valid with a single package.",
)
@click.option("-y", "--yes", is_flag=True, help="Skip plan previews and confirmations.")
@click.argument("packages", nargs=-1)
def update_cmd(packages: tuple[str, ...], target_version: str | None, yes: bool) -> None:
    """Update packages. Interactive picker with recommended updates when called with no arguments."""
    if not packages:
        if target_version:
            ui.error("--version requires a package key.")
            sys.exit(1)
        _update_interactive(yes=yes)
        return

    if target_version and len(packages) > 1:
        ui.error("--version can only be used with a single package.")
        sys.exit(1)

    tools: list[Tool] = []
    for key in packages:
        tool = registry.get(key)
        if tool is None:
            ui.error(f"Unknown package: '{key}'")
            sys.exit(1)
        tools.append(tool)

    # Report not-installed tools up front so the plan only covers real work.
    results: list[tuple[str, str, str]] = []
    to_update: list[Tool] = []
    for t in tools:
        if not t.is_installed():
            ui.warn(f"{t.name} is not installed — nothing to update.")
            ui.dim(f"Install first:  devstuff install {t.key}")
            results.append((t.key, "skip", "not installed"))
        else:
            to_update.append(t)

    if not to_update:
        _report(results)
        return

    if not yes and not _confirm_plans(to_update, target_version):
        ui.warn("Aborted.")
        return

    results += _update_all(to_update, yes=yes, version=target_version)
    _report(results)


def _update_all(
    tools: list[Tool], *, yes: bool, version: str | None = None
) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    for i, tool in enumerate(tools, 1):
        pos = f"{i}/{len(tools)}" if len(tools) > 1 else ""
        status, detail = _update_one(
            tool, version, yes=yes, position=pos, confirmed=True
        )
        results.append((tool.key, status, detail))
    return results


def _update_one(
    tool: Tool,
    version: str | None,
    *,
    yes: bool = False,
    position: str = "",
    confirmed: bool = False,
) -> tuple[str, str]:
    """Update one tool. Returns (status, detail)."""
    ui.section(f"Update {tool.name}", note=position)

    if not tool.is_installed():
        ui.warn(f"{tool.name} is not installed — nothing to update.")
        ui.dim(f"Install first:  devstuff install {tool.key}")
        return "skip", "not installed"

    if not confirmed and not yes and not _confirm_plan(tool, version):
        ui.dim("Skipped.")
        return "skip", "skipped"

    before = tool.get_version()
    try:
        after = tool.update(version=version) or tool.get_version()  # type: ignore[attr-defined]
        if before and after and before == after:
            ui.success(f"{tool.name} already up to date: {after}")
            return "ok", f"up to date ({after})"
        elif before and after:
            ui.success(f"{tool.name} updated: {before} → {after}")
            return "ok", f"{before} → {after}"
        elif after:
            ui.success(f"{tool.name} updated: {after}")
            return "ok", after
        else:
            ui.success(f"{tool.name} updated")
            return "ok", "updated"
    except Exception as exc:
        ui.error(f"Failed to update {tool.name}: {escape(str(exc))}")
        return "fail", str(exc).splitlines()[-1][:120]


def _confirm_plan(tool: Tool, version: str | None) -> bool:
    note = ""
    if tool.install_type in ("bash", "script"):
        note = "Updating re-runs this tool's full installer (may use sudo)."
    ui.plan_panel(f"Update {tool.name}", plan_for(tool, "update", version), note=note)
    return ui.confirm(f"Update {tool.name}?", default=True)


def _confirm_plans(tools: list[Tool], version: str | None) -> bool:
    if len(tools) == 1:
        return _confirm_plan(tools[0], version)
    rows = [(t.key, plan_summary(t, "update")) for t in tools]
    ui.batch_plan_panel(rows, "Update")
    return ui.confirm(f"Update {len(tools)} packages?", default=True)


def _report(results: list[tuple[str, str, str]]) -> None:
    if len(results) > 1:
        ui.summary(results, title="Update")
    if any(s == "fail" for _, s, _ in results):
        sys.exit(1)


def _update_interactive(*, yes: bool) -> None:
    ui.print_banner()

    with ui.spinner("Checking installed packages for available updates..."):
        candidates = collect_candidates()

    if not candidates:
        ui.info("No installed packages to update.")
        return

    key_width = max((len(t.key) for t, _ in candidates), default=12) + 2

    choices: list = []
    for t, status in sorted(candidates, key=lambda c: c[0].key):
        if status.available is True:
            tag = f"update available: {status.current or '?'} → {status.latest}"
            mark = "⬆ "
        elif status.available is False:
            tag = f"up to date ({status.current or status.latest})"
            mark = "  "
        else:
            tag = "unknown — reinstall to check"
            mark = "  "
        # Plain-string title so `highlighted`/`selected` reach the row.
        title = f"{mark}{t.key:<{key_width}}{tag}"
        choices.append(questionary.Choice(title=title, value=t.key, checked=status.available is True))

    selected = ui.checkbox(
        "Select packages to update:",
        choices=choices,
        instruction="(Space toggle · Enter confirm · pre-checked items have a known update)",
    )

    if not selected:
        ui.info("No packages selected.")
        return

    tools_to_update = [t for k in selected if (t := registry.get(k)) is not None]
    if not yes and not _confirm_plans(tools_to_update, None):
        ui.warn("Aborted.")
        return

    results = _update_all(tools_to_update, yes=yes)
    _report(results)
