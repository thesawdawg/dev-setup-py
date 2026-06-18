from __future__ import annotations

import re
import sys
from typing import Optional

import click

from dev_setup import registry, ui
from dev_setup.generic import GenericTool

_VALID_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_INSTALL_TEMPLATE = """\
#!/usr/bin/env bash
# Install script for {name}
# Save and close to continue. Delete all content (except this line) to abort.
set -euo pipefail

"""

_REMOVE_TEMPLATE = """\
#!/usr/bin/env bash
# Remove script for {name}
# Save and close to continue. Leave only comments/blank lines to skip removal.
set -euo pipefail

"""


@click.command("add")
def add_cmd() -> None:
    """Add a custom package via guided wizard."""
    ui.section("Add Custom Package")

    install_type = ui.select(
        "Package type:",
        ["npm", "pip", "apt", "git", "script", "bash"],
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

    elif install_type == "bash":
        kwargs["check_cmd"] = ui.text_input(
            "Command to verify installation (e.g. bat, aws):", required=False
        )
        install_script = _edit_script("install", name, required=True)
        if install_script is None:
            ui.warn("No install script provided — aborted.")
            return
        kwargs["install_script"] = install_script

        remove_script = _edit_script("remove", name, required=False)
        if remove_script:
            kwargs["remove_script"] = remove_script

    kwargs["help_cmd"] = ui.text_input(
        "Help command (optional, e.g. tool --help):", required=False
    )

    ui.console.print()
    ui.console.print("[bold]Summary[/]")
    for k, v in kwargs.items():
        if v:
            display = _truncate_script(v) if k.endswith("_script") else v
            ui.console.print(f"  [dim]{k:<18}[/] {display}")
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


def _edit_script(action: str, package_name: str, required: bool = True) -> Optional[str]:
    """Open $EDITOR with a bash template. Returns stripped script or None if aborted."""
    template = (_INSTALL_TEMPLATE if action == "install" else _REMOVE_TEMPLATE).format(
        name=package_name
    )
    label = "install" if action == "install" else "remove (optional)"
    ui.console.print()
    ui.info(f"Opening $EDITOR for the [bold]{label}[/] script...")
    ui.dim("Write your bash commands, save, and close the editor to continue.")
    ui.console.print()

    try:
        content = click.edit(text=template, extension=".sh", require_save=False)
    except Exception as exc:
        ui.error(f"Could not open editor: {exc}")
        ui.dim("Set the EDITOR environment variable (e.g. export EDITOR=nano) and try again.")
        return None

    if content is None:
        return None

    script = _strip_template_comments(content)
    if not script and required:
        ui.error("Install script cannot be empty.")
        return None
    return script or ""


def _strip_template_comments(content: str) -> str:
    """Remove comment-only lines and leading/trailing blank lines."""
    lines = [
        line for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(lines).strip()


def _truncate_script(script: str) -> str:
    lines = [l for l in script.splitlines() if l.strip()]
    if not lines:
        return "[dim](empty)[/]"
    first = lines[0][:60]
    suffix = f"  [dim]+{len(lines) - 1} more lines[/]" if len(lines) > 1 else ""
    return f"{first}{suffix}"
