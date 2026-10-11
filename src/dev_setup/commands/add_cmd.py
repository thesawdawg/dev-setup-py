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

_TYPES = ["npm", "uvx", "apt", "git", "script", "bash"]

# Catalog fields collected per type — filters out answers left over from a type
# the user backed out of and changed.
_TYPE_FIELDS = {
    "npm": ["npm_name", "check_cmd"],
    "uvx": ["pip_name", "check_cmd", "uv_executables_from", "uv_with", "uv_python"],
    "apt": ["apt_packages", "check_cmd"],
    "git": ["git_url", "git_install_cmd", "git_remove_cmd", "check_cmd"],
    "script": ["script_url", "check_cmd"],
    "bash": ["check_cmd", "install_script", "remove_script"],
}
_COMMON_FIELDS = ["help_cmd", "docs_url"]


@click.command("add")
def add_cmd() -> None:
    """Add a custom package via guided wizard."""
    ui.section("Add Custom Package")

    answers = ui.Wizard("Add package").run(_steps())
    if answers is None:
        ui.warn("Aborted.")
        return

    install_type = answers["type"]
    kwargs: dict = dict(
        key=answers["key"],
        name=answers["name"],
        description=answers.get("description", ""),
        category="custom",
        install_type=install_type,
    )
    for field in _TYPE_FIELDS.get(install_type, []) + _COMMON_FIELDS:
        if answers.get(field):
            kwargs[field] = answers[field]

    tool = GenericTool(**kwargs)
    tool.save()
    registry.register(tool)

    ui.success(f"Package '{tool.key}' added. Install with: devstuff install {tool.key}")


def _steps() -> list[ui.Step]:
    def is_type(*types: str):
        return lambda a: a.get("type") in types

    def text(key: str, label: str, prompt: str, *, required: bool = False,
             default=None, when=None, split: bool = False) -> ui.Step:
        def ask(a: dict):
            dflt = a.get(key) or (default(a) if default else "")
            if isinstance(dflt, list):
                dflt = " ".join(dflt)
            val = ui.text_input_back(prompt, default=dflt, required=required)
            if val is ui.BACK:
                return ui.BACK
            return val.split() if split and val else val

        return ui.Step(key, label, ask, when=when)

    return [
        ui.Step(
            "type", "package type",
            lambda a: ui.select_back("Package type:", _TYPES, default=a.get("type")),
        ),
        ui.Step("key", "package key", _ask_key),
        text(
            "name", "display name", "Display name:", required=True,
            default=lambda a: a.get("key", ""),
        ),
        text("description", "description", "Short description:"),
        # npm
        text(
            "npm_name", "npm package", "npm package name:", required=True,
            default=lambda a: a.get("key", ""), when=is_type("npm"),
        ),
        # uvx
        text(
            "pip_name", "PyPI package", "PyPI package name:", required=True,
            default=lambda a: a.get("key", ""), when=is_type("uvx"),
        ),
        text(
            "uv_executables_from", "executables from",
            "Also expose executables from (space-separated packages, blank for none):",
            when=is_type("uvx"), split=True,
        ),
        text(
            "uv_with", "extra packages",
            "Extra packages to install alongside it (space-separated, blank for none):",
            when=is_type("uvx"), split=True,
        ),
        text(
            "uv_python", "python pin",
            "Pin to a Python version (e.g. 3.12, blank for uv's default):",
            when=is_type("uvx"),
        ),
        # apt
        text(
            "apt_packages", "apt packages", "apt package(s) (space-separated):",
            required=True, default=lambda a: a.get("key", ""), when=is_type("apt"),
        ),
        # git
        text("git_url", "repo URL", "Git repository URL:", required=True, when=is_type("git")),
        text(
            "git_install_cmd", "install command",
            "Post-clone install command (optional, run inside repo):",
            when=is_type("git"),
        ),
        text(
            "git_remove_cmd", "remove command",
            "Pre-delete remove command (optional):", when=is_type("git"),
        ),
        # script
        text(
            "script_url", "script URL", "Install script URL (curl | sh):",
            required=True, when=is_type("script"),
        ),
        # every type collects check_cmd — required nowhere, defaulted to the key
        # for the types where the binary name almost always matches
        text(
            "check_cmd", "check command", "Command to check if installed:",
            default=lambda a: a.get("key", "") if a.get("type") in ("npm", "uvx", "apt") else "",
        ),
        # bash
        ui.Step(
            "install_script", "install script", _ask_install_script,
            when=is_type("bash"), show=_show_script,
        ),
        ui.Step(
            "remove_script", "remove script", _ask_remove_script,
            when=is_type("bash"), show=_show_script,
        ),
        text("help_cmd", "help command", "Help command (optional, e.g. tool --help):"),
        text("docs_url", "docs URL", "Documentation URL (optional):"),
    ]


def _ask_key(answers: dict):
    while True:
        key = ui.text_input_back(
            "Package key (lowercase, hyphens ok):",
            default=answers.get("key", ""),
            required=True,
        )
        if key is ui.BACK:
            return ui.BACK
        if not _VALID_KEY.match(key):
            ui.error("Key must be lowercase letters, digits, hyphens, or underscores.")
            continue
        if registry.exists(key):
            ui.error(f"A package with key '{key}' already exists.")
            continue
        return key


def _ask_install_script(answers: dict):
    script = _edit_script("install", answers.get("name", ""), required=True,
                          prefill=answers.get("install_script", ""))
    if script is None:
        ui.warn("No install script provided.")
        return ui.BACK
    return script


def _ask_remove_script(answers: dict):
    script = _edit_script(
        "remove", answers.get("name", ""), required=False,
        prefill=answers.get("remove_script", ""),
    )
    return _validate_remove_script(
        script or "",
        answers.get("install_script", ""),
        answers.get("check_cmd", ""),
        answers.get("name", ""),
    )


def _show_script(script) -> str:
    return _truncate_script(str(script))


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
