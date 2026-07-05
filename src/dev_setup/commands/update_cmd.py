from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

import click
import questionary

from dev_setup import registry, ui
from dev_setup.base import Tool
from dev_setup.generic import UpdateStatus


@click.command("update")
@click.option("--verbose", "-v", is_flag=True, help="Stream update output to the terminal.")
@click.option(
    "--version", "target_version", default=None,
    help="Update to a specific version instead of latest. Only valid with a single package.",
)
@click.argument("packages", nargs=-1)
def update_cmd(packages: tuple[str, ...], verbose: bool, target_version: str | None) -> None:
    """Update packages. Interactive picker with recommended updates when called with no arguments."""
    from dev_setup import generic
    generic._verbose = verbose

    if not packages:
        if target_version:
            ui.error("--version requires a package key.")
            sys.exit(1)
        _update_interactive()
        return

    if target_version and len(packages) > 1:
        ui.error("--version can only be used with a single package.")
        sys.exit(1)

    failed = False
    for key in packages:
        if not registry.exists(key):
            ui.error(f"Unknown package: '{key}'")
            failed = True
            continue
        if not _update_one(registry.get(key), target_version):  # type: ignore[arg-type]
            failed = True

    if failed:
        sys.exit(1)


def _update_one(tool: Tool, version: str | None) -> bool:
    ui.section(f"Update {tool.name}")

    if not tool.is_installed():
        ui.warn(f"{tool.name} is not installed — nothing to update.")
        ui.dim(f"Install first:  dev-setup install {tool.key}")
        return True

    if tool.install_type in ("bash", "script"):
        ui.warn(f"Updating {tool.name} re-runs its full installer (may use sudo).")
        if not ui.confirm(f"Continue updating {tool.name}?", default=False):
            ui.dim("Skipped.")
            return True

    before = tool.get_version()
    try:
        after = tool.update(version=version) or tool.get_version()  # type: ignore[attr-defined]
        if before and after and before == after:
            ui.success(f"{tool.name} already up to date: {after}")
        elif before and after:
            ui.success(f"{tool.name} updated: {before} → {after}")
        elif after:
            ui.success(f"{tool.name} updated: {after}")
        else:
            ui.success(f"{tool.name} updated")
        return True
    except Exception as exc:
        ui.error(f"Failed to update {tool.name}: {exc}")
        return False


def _collect_update_candidates() -> list[tuple[Tool, UpdateStatus]]:
    """Return (tool, UpdateStatus) for every installed tool, probed concurrently.

    Pure data-gathering, kept free of any UI/prompt code so it can be exercised
    directly without a terminal.
    """
    tools = registry.all_tools()
    with ThreadPoolExecutor(max_workers=8) as pool:
        installed_flags = list(pool.map(lambda t: t.is_installed(), tools))
    installed = [t for t, flag in zip(tools, installed_flags, strict=True) if flag]
    if not installed:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda t: t.check_for_update(), installed))  # type: ignore[attr-defined]
    return list(zip(installed, statuses, strict=True))


def _update_interactive() -> None:
    ui.print_banner()

    with ui.spinner("Checking installed packages for available updates..."):
        candidates = _collect_update_candidates()

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
        title = [
            ("class:check", mark),
            ("class:text", f"{t.key:<{key_width}}{tag}"),
        ]
        choices.append(questionary.Choice(title=title, value=t.key, checked=status.available is True))

    selected = questionary.checkbox(
        "Select packages to update:",
        choices=choices,
        instruction="(Space toggle · Enter confirm · pre-checked items have a known update)",
        style=ui._STYLE,
    ).ask()

    if not selected:
        ui.info("No packages selected.")
        return

    if not ui.confirm(f"Update {len(selected)} package(s)?"):
        ui.warn("Aborted.")
        return

    failed = False
    for key in selected:
        tool = registry.get(key)
        if tool and not _update_one(tool, None):
            failed = True

    if failed:
        sys.exit(1)
