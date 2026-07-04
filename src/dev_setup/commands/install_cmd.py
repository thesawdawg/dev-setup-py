from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

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

    # Probe installed status concurrently — each check shells out.
    with ui.spinner("Checking installed packages..."), ThreadPoolExecutor(max_workers=8) as pool:
        statuses = pool.map(lambda t: t.is_installed(), tools)
        installed = dict(zip((t.key for t in tools), statuses, strict=True))

    by_cat: dict[str, list[Tool]] = {}
    for t in tools:
        by_cat.setdefault(t.category, []).append(t)

    key_width = max((len(t.key) for t in tools), default=12) + 2
    desc_width = max(20, ui.console.width - key_width - 14)

    choices: list = []
    _ORDER = {"core": 0, "tools": 1, "custom": 999}
    for cat in sorted(by_cat, key=lambda c: (_ORDER.get(c, 500), c)):
        entries = by_cat[cat]
        n_inst = sum(installed[t.key] for t in entries)
        choices.append(questionary.Separator(
            f"\n  {cat.upper()}  ({n_inst}/{len(entries)} installed)"
        ))
        for t in entries:
            is_inst = installed[t.key]
            missing = [] if is_inst else registry.missing_requires(t)
            desc = t.description
            if len(desc) > desc_width:
                desc = desc[: desc_width - 1] + "…"
            if is_inst:
                disabled = "installed ✔"
            elif missing:
                disabled = f"requires {', '.join(missing)}"
            else:
                disabled = None
            choices.append(questionary.Choice(
                title=f"{t.key:<{key_width}}{desc}",
                value=t.key,
                checked=False,
                disabled=disabled,
            ))

    selected = questionary.checkbox(
        "Select packages to install:",
        choices=choices,
        instruction="(Space toggle · Enter confirm · installed items are skipped)",
        style=ui._STYLE,
    ).ask()

    if not selected:
        ui.info("No packages selected.")
        return

    to_install = selected

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
