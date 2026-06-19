from __future__ import annotations

import sys

import click

from dev_setup import registry, ui
from dev_setup.generic import CUSTOM_DIR


@click.command("delete")
@click.argument("key")
def delete_cmd(key: str) -> None:
    """Remove a custom package from the registry."""
    if not registry.exists(key):
        ui.error(f"No package with key '{key}' found.")
        sys.exit(1)

    tool = registry.get(key)
    assert tool is not None

    if tool.builtin:
        ui.error(f"'{key}' is a built-in package and cannot be deleted.")
        sys.exit(1)

    pkg_file = CUSTOM_DIR / f"{key}.json"
    if not pkg_file.exists():
        ui.error(f"Package file not found: {pkg_file}")
        sys.exit(1)

    ui.console.print(f"\n  [bold]{tool.name}[/] — {tool.description}\n")

    if not ui.confirm(f"Delete '{key}' from the registry?", default=False):
        ui.dim("Aborted.")
        return

    pkg_file.unlink()
    registry.deregister(key)
    ui.success(f"Package '{key}' deleted from registry.")
