from __future__ import annotations

import sys

import click

from dev_setup import function_runner as runner
from dev_setup import functions_registry, ui
from dev_setup.functions_catalog import USER_CATALOG_PATH


@click.group("functions")
def functions_cmd() -> None:
    """Manage functions/scripts. Invoke one with: devstuff run <key>."""


@functions_cmd.command("list")
def list_cmd() -> None:
    """List available functions."""
    fns = functions_registry.all_functions()
    if not fns:
        ui.info("No functions defined.")
        return

    by_cat: dict[str, list] = {}
    for f in fns:
        by_cat.setdefault(f.category, []).append(f)

    for cat in sorted(by_cat, key=lambda c: (c == "custom", c)):
        entries = sorted(by_cat[cat], key=lambda f: f.key)
        tbl = ui.table(
            title=f"[bold]{cat}[/]  [{ui.GRAY}]{len(entries)} function(s)[/]"
        )
        tbl.add_column("Function", style="bold cyan", no_wrap=True)
        tbl.add_column("Type", style=ui.CYAN, no_wrap=True)
        tbl.add_column("Args", style=ui.GRAY, no_wrap=True)
        tbl.add_column("Description", ratio=1)
        for f in entries:
            mode = f.type if f.type == "script" else f"{f.type} ({f.register})"
            params = " ".join(f"<{p.name}>" for p in f.params)
            tbl.add_row(f.key, mode, params, f.description)
        ui.console.print(tbl)
        ui.console.print()


@functions_cmd.command("enable")
@click.argument("key")
def enable_cmd(key: str) -> None:
    """Register a bashrc-backed function into ~/.bashrc."""
    fn = functions_registry.get(key)
    if fn is None:
        ui.error(f"Unknown function: '{key}'")
        sys.exit(1)
    if not (fn.type == "shell-eval" and fn.register == "bashrc"):
        ui.error(f"'{fn.key}' does not use bashrc registration.")
        if fn.type == "script":
            ui.dim(f"Run it directly:  devstuff run {fn.key}")
        else:
            ui.dim(f'Use:  eval "$(devstuff run {fn.key} ...)"')
        sys.exit(1)

    added = runner.enable_bashrc_function(fn)
    if added:
        ui.success(f"Registered '{fn.key}' in ~/.bashrc")
        ui.dim("Run: source ~/.bashrc   (or open a new shell)")
        args = " ".join(f"<{p.name}>" for p in fn.params)
        ui.dim(f"Then call it directly:  {fn.key} {args}".rstrip())
    else:
        ui.info(f"'{fn.key}' is already registered in ~/.bashrc")


@functions_cmd.command("disable")
@click.argument("key")
def disable_cmd(key: str) -> None:
    """Remove a bashrc-backed function from ~/.bashrc."""
    fn = functions_registry.get(key)
    if fn is None:
        ui.error(f"Unknown function: '{key}'")
        sys.exit(1)

    removed = runner.disable_bashrc_function(fn)
    if removed:
        ui.success(f"Removed '{fn.key}' from ~/.bashrc")
    else:
        ui.info(f"'{fn.key}' is not registered in ~/.bashrc")


@functions_cmd.command("path")
def path_cmd() -> None:
    """Print the path to the user functions catalog."""
    click.echo(str(USER_CATALOG_PATH))
