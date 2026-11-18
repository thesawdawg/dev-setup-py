from __future__ import annotations

import sys
from pathlib import Path

import click
import questionary
from rich.markup import escape

from dev_setup import configure, registry, ui


def can_prompt() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def require_prompt(message: str) -> None:
    if not can_prompt():
        raise click.UsageError(message)


@click.command("configure")
@click.argument("tool", required=False)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the result here instead of the tool's real config file.",
)
@click.option("--path", "show_path", is_flag=True, help="Print the tool's config path and exit.")
@click.option("--list", "list_only", is_flag=True, help="List tools that have a configurator.")
def configure_cmd(
    tool: str | None,
    output: Path | None,
    show_path: bool,
    list_only: bool,
) -> None:
    """Configure an installed tool with a guided wizard."""
    if list_only:
        _print_list()
        return

    if tool is None:
        require_prompt("The configurator picker needs a terminal. Use --list to see the available tools.")
        tool = _pick()
        if tool is None:
            return

    spec = configure.get(tool)
    if spec is None:
        ui.error(f"No configurator for '{tool}'.")
        _print_list()
        sys.exit(1)

    if not show_path:
        require_prompt(f"Run 'devstuff configure {spec.key}' in a terminal to use the wizard.")

    module = spec.load()
    if show_path:
        click.echo(str(module.config_path()))
        return

    if not _is_installed(spec.key):
        ui.warn(f"{spec.label} is not installed — the wizard can still write a config.")
        ui.dim(f"  Install it with:  devstuff install {spec.key}")
        if not ui.confirm("Carry on anyway?", default=True):
            return

    try:
        module.run(target=output)
    except OSError as exc:
        ui.error(f"Could not write the configuration: {escape(str(exc))}")
        sys.exit(1)


def offer_after_install(key: str) -> None:
    """Post-install hook: point out the wizard for a tool that has one (FR-14).

    Called from `install_cmd`; silent for tools without a configurator.
    """
    if not can_prompt() or (spec := configure.get(key)) is None:
        return
    ui.console.print()
    if not ui.confirm(
        f"Optional — not part of the install: Set up {spec.label} now? ({spec.description.lower()})",
        default=True,
    ):
        ui.dim(f"You can run it later:  devstuff configure {spec.key}")
        return
    try:
        spec.load().run()
    except OSError as exc:
        ui.error(f"Could not write the configuration: {escape(str(exc))}")


def _is_installed(key: str) -> bool:
    tool = registry.get(key)
    return bool(tool and tool.is_installed())


def _print_list() -> None:
    ui.console.print()
    tbl = ui.table(title="[bold]configurable tools[/]")
    tbl.add_column("", width=2, justify="center")
    tbl.add_column("Tool", style="bold cyan", no_wrap=True)
    tbl.add_column("Description", ratio=1)
    for spec in configure.CONFIGURATORS.values():
        mark = f"[{ui.GREEN}]●[/]" if _is_installed(spec.key) else f"[{ui.GRAY}]○[/]"
        tbl.add_row(mark, spec.key, spec.description)
    ui.console.print(tbl)
    ui.console.print()
    ui.dim("  devstuff configure <tool>")
    ui.console.print()


def _pick() -> str | None:
    choices = [
        questionary.Choice(
            title=f"{spec.key:<12} {spec.description}"
            + ("" if _is_installed(spec.key) else "  (not installed)"),
            value=spec.key,
        )
        for spec in configure.CONFIGURATORS.values()
    ]
    return ui.select("Configure which tool?", choices) or None
