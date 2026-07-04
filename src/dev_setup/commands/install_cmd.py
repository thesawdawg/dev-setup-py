from __future__ import annotations

import sys

import click
import questionary

from dev_setup import registry, ui
from dev_setup.base import Tool


@click.command("install")
@click.option("--verbose", "-v", is_flag=True, help="Stream install output to the terminal.")
@click.argument("packages", nargs=-1)
def install_cmd(packages: tuple[str, ...], verbose: bool) -> None:
    """Install packages. Interactive picker when called with no arguments."""
    from dev_setup import generic
    generic._verbose = verbose

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
    missing = registry.missing_requires(tool)
    if missing:
        ui.error(f"Cannot install {tool.name} — missing required tools: {', '.join(missing)}")
        ui.dim(f"Install first:  dev-setup install {' '.join(missing)}")
        return False
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

    choices = []
    for t in tools:
        is_inst = t.is_installed()
        missing = registry.missing_requires(t) if not is_inst else []
        label = (
            f"{'[installed] ' if is_inst else ''}"
            f"{t.key:<14} {t.description}"
        )
        choices.append(questionary.Choice(
            title=label,
            value=t.key,
            checked=False,
            disabled=f"requires {', '.join(missing)}" if missing else False,
        ))

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
