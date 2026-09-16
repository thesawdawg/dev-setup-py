from __future__ import annotations

import sys
from pathlib import Path

import click
import questionary

from dev_setup import configure, registry, ui
from dev_setup.catalog import CatalogError


@click.command("configure")
@click.argument("tool", required=False)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the result here instead of the tool's real config file.",
)
@click.option("--path", "show_path", is_flag=True, help="Print the tool's config path and exit.")
@click.option(
    "--export",
    "export",
    default=None,
    is_flag=False,
    flag_value="",
    metavar="[ARG]",
    help="Print the configurator's shell bridge to stdout and exit (not every tool has one).",
)
@click.option("--list", "list_only", is_flag=True, help="List tools that have a configurator.")
def configure_cmd(
    tool: str | None,
    output: Path | None,
    show_path: bool,
    export: str | None,
    list_only: bool,
) -> None:
    """Configure an installed tool with a guided wizard."""
    if list_only:
        _print_list()
        return

    interactive = sys.stdin.isatty()

    if tool is None:
        if not interactive:
            ui.error("Specify a tool to configure.")
            _print_list()
            sys.exit(1)
        tool = _pick()
        if tool is None:
            return

    spec = configure.get(tool)
    if spec is None:
        ui.error(f"No configurator for '{tool}'.")
        _print_list()
        sys.exit(1)

    module = spec.load()

    if export is not None:
        _export(spec, module, export)
        return

    if show_path:
        click.echo(str(module.config_path()))
        return

    if not interactive:
        ui.error(f"devstuff configure {spec.key} needs a terminal.")
        sys.exit(1)

    if not _is_installed(spec):
        ui.warn(f"{spec.label} is not installed — the wizard can still write a config.")
        ui.dim(f"  Install it with:  devstuff install {spec.key}")
        if not ui.confirm("Carry on anyway?", default=True):
            return

    try:
        module.run(target=output)
    except (KeyboardInterrupt, EOFError):
        ui.console.print()
        ui.dim("Cancelled — nothing was written.")
    except OSError as exc:
        ui.error(f"Could not write the configuration: {exc}")
        sys.exit(1)


def offer_after_install(key: str) -> None:
    """Post-install hook: point out the wizard for a tool that has one (FR-14).

    Called from `install_cmd`; silent for tools without a configurator.
    """
    spec = configure.get(key)
    if spec is None or not sys.stdin.isatty():
        return
    ui.console.print()
    if not ui.confirm(f"Set up {spec.label} now? ({spec.description.lower()})", default=True):
        ui.dim(f"You can run it later:  devstuff configure {spec.key}")
        return
    try:
        spec.load().run()
    except (KeyboardInterrupt, EOFError):
        ui.console.print()
        ui.dim(f"Skipped — run it later with:  devstuff configure {spec.key}")
    except OSError as exc:
        ui.error(f"Could not write the configuration: {exc}")


def _export(spec: configure.Configurator, module, arg: str) -> None:
    """Print a configurator's shell bridge — the `--export` path.

    stdout carries the assignments and nothing else, for the same reason a
    `register: eval` function's does: the caller runs `eval "$(devstuff configure x
    --export)"`, so a stray `ui.*` line would be executed by their shell. Every
    diagnostic here goes to stderr, and a failure exits non-zero with stdout empty.
    """
    exporter = getattr(module, "export", None)
    if exporter is None:
        click.echo(f"'{spec.key}' has no shell export.", err=True)
        sys.exit(1)
    try:
        click.echo(exporter(arg or None))
    except (CatalogError, OSError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)


def _is_installed(spec: configure.Configurator) -> bool:
    """Whether this wizard can do its job on this machine.

    For a standalone configurator that is always true: there is no package behind it to
    be missing, so the mark in the list means "ready to run", not "binary present".
    """
    if spec.standalone:
        return True
    tool = registry.get(spec.key)
    return bool(tool and tool.is_installed())


def _print_list() -> None:
    ui.console.print()
    ui.console.print("  [bold]CONFIGURABLE TOOLS[/]")
    width = max(len(s.key) for s in configure.CONFIGURATORS.values()) + 2
    for spec in configure.CONFIGURATORS.values():
        mark = "[green]✔[/]" if _is_installed(spec) else "[dim]·[/]"
        ui.console.print(f"  {mark} [bold cyan]{spec.key:<{width}}[/] {spec.description}")
    ui.console.print()
    ui.dim("  devstuff configure <tool>")
    ui.console.print()


def _pick() -> str | None:
    choices = [
        questionary.Choice(
            title=f"{spec.key:<12} {spec.description}"
            + ("" if _is_installed(spec) else "  (not installed)"),
            value=spec.key,
        )
        for spec in configure.CONFIGURATORS.values()
    ]
    return ui.select("Configure which tool?", choices) or None
