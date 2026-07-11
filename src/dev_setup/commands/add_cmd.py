from __future__ import annotations

import re

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
        ["npm", "uvx", "apt", "git", "script", "bash"],
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

    elif install_type == "uvx":
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
        remove_script = _validate_remove_script(
            remove_script or "",
            install_script,
            kwargs.get("check_cmd", ""),
            name,
        )
        if remove_script:
            kwargs["remove_script"] = remove_script

    kwargs["help_cmd"] = ui.text_input(
        "Help command (optional, e.g. tool --help):", required=False
    )
    kwargs["docs_url"] = ui.text_input(
        "Documentation URL (optional):", required=False
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

    from dev_setup import registry
    registry.register(tool)

    ui.success(f"Package '{key}' added. Install with: devthings install {key}")


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


def _edit_script(
    action: str,
    package_name: str,
    required: bool = True,
    prefill: str = "",
    quiet: bool = False,
) -> str | None:
    """Open $EDITOR with a bash template. Returns stripped script or None if aborted."""
    if prefill:
        template = prefill
    else:
        template = (_INSTALL_TEMPLATE if action == "install" else _REMOVE_TEMPLATE).format(
            name=package_name
        )
    if not quiet:
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


def _is_safe_literal_path(path: str) -> bool:
    """Return True only for fully literal paths — no shell expansion or injection chars."""
    return not any(c in path for c in ('$', '`', '(', ')', ';', '&', '|', ' ', '"', "'", '\\'))


def _extract_installed_paths(install_script: str) -> list[str]:
    """Find definitive binary install destinations (literal paths only)."""
    paths: list[str] = []

    # curl ... -o /path/binary
    for m in re.finditer(r'\bcurl\b[^|\n]*?-o\s+(\S+)', install_script):
        p = m.group(1).strip()
        if (
            p.startswith(("/", "~/"))
            and _is_safe_literal_path(p)
            and not p.endswith(('.sh', '.tar.gz', '.zip', '.tmp', '.tar', '.bz2', '.gz'))
        ):
            paths.append(p)

    # sudo mv /tmp/x /usr/local/bin/y  — destination only
    for m in re.finditer(
        r'\bmv\s+\S+\s+((?:/usr|/opt)[\w./+-]*)', install_script
    ):
        p = m.group(1).strip()
        if _is_safe_literal_path(p) and "." not in p.split("/")[-1]:
            paths.append(p)

    return list(dict.fromkeys(paths))


def _suggest_remove_script(install_script: str, check_cmd: str, name: str) -> str:
    """Generate a best-effort remove script from patterns in the install script."""
    svc_lines: list[str] = []
    actions: list[str] = []

    # systemd service teardown (must precede file removal)
    for m in re.finditer(r'\bsystemctl\s+(?:enable|start)\s+(\S+)', install_script):
        svc = m.group(1).rstrip(";")
        if not any(svc in a for a in svc_lines):
            svc_lines.append(f"sudo systemctl stop {svc} 2>/dev/null || true")
            svc_lines.append(f"sudo systemctl disable {svc} 2>/dev/null || true")

    # apt packages
    apt_packages: list[str] = []
    for m in re.finditer(r'\bapt(?:-get)?\s+install\s+(?:-y\s+)?(.+)', install_script):
        for pkg in m.group(1).split():
            if not pkg.startswith("-") and pkg not in apt_packages:
                apt_packages.append(pkg)
    if apt_packages:
        actions.append(f"sudo apt-get remove -y {' '.join(apt_packages)}")

    # Binary paths from curl / mv (skip /tmp — temp downloads are gone after mv)
    for path in _extract_installed_paths(install_script):
        if path.startswith("/tmp/"):
            continue
        prefix = "sudo " if path.startswith(("/usr/", "/opt/", "/etc/")) else ""
        actions.append(f"{prefix}rm -rf {path}")

    # Fallback: derive removal from check_cmd if nothing else found
    if not actions and not svc_lines and check_cmd:
        actions.append(f'CMD=$(command -v {check_cmd} 2>/dev/null || true)')
        actions.append('[ -n "$CMD" ] && sudo rm -f "$CMD"')

    if not svc_lines and not actions:
        return ""

    lines = ["#!/usr/bin/env bash", "set -euo pipefail", ""] + svc_lines + actions
    return "\n".join(lines)


def _remove_needs_review(remove_script: str, install_script: str) -> bool:
    """Return True when the remove script warrants a review prompt."""
    if not remove_script.strip():
        return True

    installed_paths = _extract_installed_paths(install_script)
    if not installed_paths:
        return False  # can't tell — don't be noisy

    def _references_target(path: str) -> bool:
        parts = [p for p in path.rstrip("/").split("/") if p]
        candidates = [path]
        if parts:
            candidates.append(parts[-1])  # basename
        for i in range(2, len(parts)):  # ancestor dirs
            candidates.append("/" + "/".join(parts[:i]))
        return any(c in remove_script for c in candidates)

    # Flag only when NONE of the detected targets is referenced
    return not any(_references_target(p) for p in installed_paths)


def _validate_remove_script(
    remove_script: str,
    install_script: str,
    check_cmd: str,
    name: str,
) -> str:
    """If the remove script looks empty or mismatched, offer a recommended alternative."""
    if not _remove_needs_review(remove_script, install_script):
        return remove_script

    recommended = _suggest_remove_script(install_script, check_cmd, name)

    ui.console.print()
    if not remove_script.strip():
        ui.warn("Remove script is empty — uninstalling will fail without one.")
    else:
        ui.warn("Remove script may not undo the installation (possible path mismatch).")

    if recommended:
        ui.console.print()
        ui.info("Suggested remove script:")
        ui.console.print()
        ui.code_block(recommended)
        ui.console.print()

    choices: list[str] = []
    if recommended:
        choices.append("Use recommended")
    choices.append("Keep mine (empty)" if not remove_script.strip() else "Keep mine")
    choices.append("Edit manually")

    choice = ui.select("How would you like to proceed?", choices)

    if choice == "Use recommended":
        return _strip_template_comments(recommended)
    if choice == "Edit manually":
        prefill = recommended or remove_script or _REMOVE_TEMPLATE.format(name=name)
        edited = _edit_script("remove", name, required=False, prefill=prefill, quiet=True)
        return edited if edited is not None else remove_script
    return remove_script  # "Keep mine" / "Keep mine (empty)"


def _truncate_script(script: str) -> str:
    lines = [ln for ln in script.splitlines() if ln.strip()]
    if not lines:
        return "[dim](empty)[/]"
    first = lines[0][:60]
    suffix = f"  [dim]+{len(lines) - 1} more lines[/]" if len(lines) > 1 else ""
    return f"{first}{suffix}"
