from __future__ import annotations

import sys
from typing import Tuple

import click
import questionary

from dev_setup import registry, ui
from dev_setup.base import Tool


@click.command("install")
@click.argument("packages", nargs=-1)
def install_cmd(packages: Tuple[str, ...]) -> None:
    """Install packages. Interactive picker when called with no arguments."""
    if not packages:
        _install_interactive()
    else:
        failed = False
        for key in packages:
            if not registry.exists(key):
                ui.error(f"Unknown package: '{key}'")
                failed = True
                continue
            if not _install_one(registry.get(key)):  # type: ignore[arg-type]
                failed = True
        if failed:
            sys.exit(1)


def _install_one(tool: Tool) -> bool:
    ui.section(tool.name)
    if tool.is_installed():
        ui.success(f"{tool.name} is already installed: {tool.get_version()}")
        return True
    try:
        version = tool.install()
        msg = f"{tool.name} installed"
        if version:
            msg += f": {version}"
        ui.success(msg)
        return True
    except Exception as exc:
        ui.error(f"Failed to install {tool.name}: {exc}")
        return False


def _install_interactive() -> None:
    ui.print_banner()
    tools = registry.all_tools()

    choices = [
        questionary.Choice(
            title=(
                f"{'[installed] ' if t.is_installed() else ''}"
                f"{t.key:<14} {t.description}"
            ),
            value=t.key,
            checked=False,
        )
        for t in tools
    ]

    selected = questionary.checkbox(
        "Select packages to install  (Space to toggle, Enter to confirm):",
        choices=choices,
        style=ui._STYLE,
    ).ask()

    if not selected:
        ui.info("No packages selected.")
        return

    already = [k for k in selected if registry.get(k) and registry.get(k).is_installed()]  # type: ignore[union-attr]
    to_install = [k for k in selected if k not in already]

    if already:
        ui.dim(f"Already installed: {', '.join(already)}")

    if not to_install:
        ui.info("Nothing new to install.")
        return

    if not ui.confirm(f"Install {len(to_install)} package(s)?"):
        ui.warn("Aborted.")
        return

    failed = False
    for key in to_install:
        tool = registry.get(key)
        if tool and not _install_one(tool):
            failed = True

    if failed:
        sys.exit(1)
