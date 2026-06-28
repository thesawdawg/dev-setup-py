from __future__ import annotations

import sys
import webbrowser

import click

from dev_setup import registry, ui


@click.command("docs")
@click.argument("package")
def docs_cmd(package: str) -> None:
    """Open the documentation site for a package in your browser."""
    if not registry.exists(package):
        ui.error(f"Unknown package: '{package}'")
        sys.exit(1)

    tool = registry.get(package)
    assert tool is not None

    if not tool.docs_url:
        ui.warn(f"No documentation URL is configured for '{package}'.")
        ui.dim("For custom packages, add docs_url in your YAML catalog.")
        sys.exit(1)

    ui.console.print(f"\n  [bold]{tool.name}[/] docs")
    ui.console.print(f"  [cyan]{tool.docs_url}[/]\n")

    opened = webbrowser.open(tool.docs_url)
    if opened:
        ui.success("Opened in browser.")
    else:
        ui.warn("Could not open a browser — copy the URL above to open manually.")
