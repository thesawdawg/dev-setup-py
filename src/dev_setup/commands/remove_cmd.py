from __future__ import annotations

import sys
from typing import Tuple

import click

from dev_setup import registry, ui
from dev_setup.base import Tool


@click.command("remove")
@click.argument("packages", nargs=-1)
def remove_cmd(packages: Tuple[str, ...]) -> None:
    """Uninstall installed packages."""
    if not packages:
        ui.error("Specify at least one package key. See: dev-setup list --installed")
        sys.exit(1)

    failed = False
    for key in packages:
        if not registry.exists(key):
            ui.error(f"Unknown package: '{key}'")
            failed = True
            continue
        if not _remove_one(registry.get(key)):  # type: ignore[arg-type]
            failed = True

    if failed:
        sys.exit(1)


def _remove_one(tool: Tool) -> bool:
    ui.section(f"Remove {tool.name}")

    if not tool.is_installed():
        ui.warn(f"{tool.name} is not installed — nothing to remove.")
        return True

    if not ui.confirm(f"Remove {tool.name}?", default=False):
        ui.dim("Skipped.")
        return True

    try:
        tool.remove()
        ui.success(f"{tool.name} removed")
        return True
    except Exception as exc:
        ui.error(f"Failed to remove {tool.name}: {exc}")
        return False
