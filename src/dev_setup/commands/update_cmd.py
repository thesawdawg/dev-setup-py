from __future__ import annotations

import sys

import click

from dev_setup import registry, ui
from dev_setup.base import Tool


@click.command("update")
@click.option("--verbose", "-v", is_flag=True, help="Stream update output to the terminal.")
@click.option(
    "--version", "target_version", default=None,
    help="Update to a specific version instead of latest. Only valid with a single package.",
)
@click.argument("packages", nargs=-1)
def update_cmd(packages: tuple[str, ...], verbose: bool, target_version: str | None) -> None:
    """Update installed packages to the latest (or a specified) version."""
    from dev_setup import generic
    generic._verbose = verbose

    if not packages:
        ui.error("Specify at least one package key. See: dev-setup list --installed")
        sys.exit(1)

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
