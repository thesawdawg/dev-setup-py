from __future__ import annotations

import re
import sys

import click

from dev_setup import registry, ui
from dev_setup.generic import GenericTool

_VALID_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@click.command("add")
def add_cmd() -> None:
    """Add a custom package via guided wizard."""
    ui.section("Add Custom Package")

    install_type = ui.select(
        "Package type:",
        ["npm", "pip", "apt", "git", "script"],
    )
    if not install_type:
        ui.warn("Aborted.")
        return

    key = _prompt_key()
    if not key:
        return

    name = ui.text_input("Display name:", default=key, required=True)
    description = ui.text_input("Short description:", required=False)

    kwargs: dict = dict(
        key=key,
        name=name,
        description=description,
        category="custom",
        install_type=install_type,
    )

    if install_type == "npm":
        kwargs["npm_name"] = ui.text_input("npm package name:", default=key, required=True)
        kwargs["check_cmd"] = ui.text_input("Command to check if installed:", default=key)

    elif install_type == "pip":
        kwargs["pip_name"] = ui.text_input("PyPI package name:", default=key, required=True)
        kwargs["check_cmd"] = ui.text_input("Command to check if installed:", default=key)

    elif install_type == "apt":
        kwargs["apt_packages"] = ui.text_input(
            "apt package(s) (space-separated):", default=key, required=True
        )
        kwargs["check_cmd"] = ui.text_input("Command to check if installed:", default=key)

    elif install_type == "git":
        kwargs["git_url"] = ui.text_input("Git repository URL:", required=True)
        kwargs["git_install_cmd"] = ui.text_input(
            "Post-clone install command (optional, run inside repo):", required=False
        )
        kwargs["git_remove_cmd"] = ui.text_input(
            "Pre-delete remove command (optional):", required=False
        )
        kwargs["check_cmd"] = ui.text_input("Command to check if installed:", required=False)

    elif install_type == "script":
        kwargs["script_url"] = ui.text_input("Install script URL (curl | sh):", required=True)
        kwargs["check_cmd"] = ui.text_input("Command to check if installed:", required=False)

    ui.console.print()
    ui.console.print("[bold]Summary[/]")
    for k, v in kwargs.items():
        if v:
            ui.console.print(f"  [dim]{k:<18}[/] {v}")
    ui.console.print()

    if not ui.confirm("Save this package?"):
        ui.warn("Aborted — package not saved.")
        return

    tool = GenericTool(**kwargs)
    tool.save()

    from dev_setup import registry as _reg
    _reg._registry[key] = tool
    if key not in _reg._order:
        _reg._order.append(key)

    ui.success(f"Package '{key}' added. Install with: dev-setup install {key}")


def _prompt_key() -> str:
    while True:
        key = ui.text_input("Package key (lowercase, hyphens ok):", required=True)
        if not _VALID_KEY.match(key):
            ui.error("Key must be lowercase letters, digits, hyphens, or underscores.")
            continue
        if registry.exists(key):
            ui.error(f"A package with key '{key}' already exists.")
            if not ui.confirm("Use a different key?"):
                return ""
            continue
        return key
