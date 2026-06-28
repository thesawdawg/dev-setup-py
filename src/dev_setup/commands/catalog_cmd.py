from __future__ import annotations

from pathlib import Path

import click

from dev_setup import catalog, ui


@click.group("catalog")
def catalog_cmd() -> None:
    """Manage YAML tool catalogs."""


@catalog_cmd.command("path")
def path_cmd() -> None:
    """Print the user catalog path."""
    click.echo(catalog.USER_CATALOG_PATH)


@catalog_cmd.command("export")
@click.argument("path", required=False, type=click.Path(path_type=Path))
def export_cmd(path: Path | None) -> None:
    """Export the effective catalog to PATH."""
    out = path or Path("dev-setup-tools.yaml")
    catalog.export_catalog(out)
    ui.success(f"Exported catalog to {out}")


@catalog_cmd.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_cmd(path: Path) -> None:
    """Import and merge a YAML catalog into the user catalog."""
    keys = catalog.import_catalog(path)
    ui.success(f"Imported {len(keys)} package(s) into {catalog.USER_CATALOG_PATH}")


