from __future__ import annotations

import sys

import click

from dev_setup import catalog, registry, ui


@click.command("delete")
@click.argument("key")
def delete_cmd(key: str) -> None:
    """Remove a custom package from the registry."""
    if not registry.exists(key):
        ui.error(f"No package with key '{key}' found.")
        sys.exit(1)

    tool = registry.get(key)
    assert tool is not None

    if not catalog.user_has_tool(key):
        ui.error(f"'{key}' is a built-in package and cannot be deleted.")
        sys.exit(1)

    ui.console.print(f"\n  [bold]{tool.name}[/] — {tool.description}\n")

    if not ui.confirm(f"Delete user catalog entry '{key}'?", default=False):
        ui.dim("Aborted.")
        return

    catalog.delete_user_tool(key)
    registry.reload()
    if registry.exists(key):
        ui.success(f"User override for '{key}' removed — built-in version is now active.")
    else:
        ui.success(f"Package '{key}' deleted from user catalog.")
