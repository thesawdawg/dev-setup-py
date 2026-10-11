from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

import click
import questionary
from rich.markup import escape

from dev_setup import registry, ui
from dev_setup.base import Tool
from dev_setup.commands._plan import plan_for, plan_summary


@click.command("install")
@click.option("-y", "--yes", is_flag=True, help="Skip plan previews and confirmations.")
@click.argument("packages", nargs=-1)
def install_cmd(packages: tuple[str, ...], yes: bool) -> None:
    """Install packages. Interactive picker when called with no arguments."""
    if not packages:
        _install_interactive(yes=yes)
        return

    tools: list[Tool] = []
    for key in packages:
        tool = registry.get(key)
        if tool is None:
            ui.error(f"Unknown package: '{key}'")
            sys.exit(1)
        tools.append(tool)

    # Report already-installed tools up front so the plan only covers real work.
    results: list[tuple[str, str, str]] = []
    to_install: list[Tool] = []
    for t in tools:
        if t.is_installed():
            version = t.get_version()
            ui.success(f"{t.name} is already installed: {version}")
            results.append(
                (t.key, "skip", f"already installed{': ' + version if version else ''}")
            )
        else:
            to_install.append(t)

    if not to_install:
        _report(results)
        return

    if not yes and not _confirm_plans(to_install):
        ui.warn("Aborted.")
        return

    results += _install_all(to_install, yes=yes, confirmed=True)
    _report(results)


def install_by_key(key: str, *, yes: bool = False) -> bool:
    """Install one tool by catalog key, reporting through the normal install output.

    For callers outside the command layer — a configurator that offers to install a
    prerequisite it has found missing, rather than telling the user to run a second
    command and come back.
    """
    tool = registry.get(key)
    if tool is None:
        ui.error(f"Unknown package: '{key}'")
        return False
    status, _detail = _install_one(tool, yes=yes)
    # "skip" covers both already-installed (success for the caller's purpose)
    # and declined-plan — the installed probe distinguishes them.
    return status == "ok" or tool.is_installed()


def _install_all(
    tools: list[Tool], *, yes: bool, confirmed: bool = False
) -> list[tuple[str, str, str]]:
    """Install each tool in order, tracking a summary row per tool."""
    results: list[tuple[str, str, str]] = []
    for i, tool in enumerate(tools, 1):
        pos = f"{i}/{len(tools)}" if len(tools) > 1 else ""
        status, detail = _install_one(tool, yes=yes, position=pos, confirmed=confirmed)
        results.append((tool.key, status, detail))
    return results


def _install_one(
    tool: Tool, *, yes: bool = False, position: str = "", confirmed: bool = False
) -> tuple[str, str]:
    """Install one tool. Returns (status, detail): status is "ok" | "skip" | "fail"."""
    ui.section(tool.name, note=position)

    if tool.is_installed():
        version = tool.get_version()
        ui.success(f"{tool.name} is already installed: {version}")
        return "skip", f"already installed{': ' + version if version else ''}"

    missing = registry.missing_requires(tool)
    if missing:
        unresolvable = [k for k in missing if not registry.exists(k)]
        if unresolvable:
            ui.error(
                f"Cannot install {tool.name} — unknown required tools: "
                f"{', '.join(unresolvable)}"
            )
            return "fail", "unknown requires"
        if yes or ui.confirm(
            f"{tool.name} requires {', '.join(missing)} — install them first?",
            default=True,
        ):
            for dep_key in missing:
                dep = registry.get(dep_key)
                status, detail = _install_one(dep, yes=yes) if dep else ("fail", "")
                if status == "fail":
                    return "fail", f"required tool {dep_key} failed"
        else:
            ui.error(f"Cannot install {tool.name} — missing: {', '.join(missing)}")
            return "fail", "declined required tools"

    if not confirmed and not yes and not _confirm_plan(tool):
        ui.dim("Skipped.")
        return "skip", "skipped"

    try:
        version = tool.install()
        msg = f"{tool.name} installed"
        if version:
            msg += f": {version}"
        ui.success(msg)
    except Exception as exc:
        ui.error(f"Failed to install {tool.name}: {escape(str(exc))}")
        return "fail", str(exc).splitlines()[-1][:120]

    # A freshly installed tool with a setup wizard is the moment to mention it.
    from dev_setup.commands.configure_cmd import offer_after_install
    offer_after_install(tool.key)
    return "ok", version or "installed"


def _confirm_plan(tool: Tool) -> bool:
    """Single-tool plan preview + confirm. Returns True to proceed."""
    ui.plan_panel(f"Install {tool.name}", plan_for(tool, "install"))
    return ui.confirm(f"Install {tool.name}?", default=True)


def _confirm_plans(tools: list[Tool]) -> bool:
    """Batch plan preview — one row per tool, one confirm for the run."""
    if len(tools) == 1:
        return _confirm_plan(tools[0])
    rows = [(t.key, plan_summary(t, "install")) for t in tools]
    ui.batch_plan_panel(rows, "Install")
    return ui.confirm(f"Install {len(tools)} packages?", default=True)


def _report(results: list[tuple[str, str, str]]) -> None:
    """Print the summary table and exit non-zero if anything failed."""
    if len(results) > 1:
        ui.summary(results, title="Install")
    if any(s == "fail" for _, s, _ in results):
        sys.exit(1)


def _install_interactive(*, yes: bool) -> None:
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
        entries = sorted(by_cat[cat], key=lambda t: t.key)
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
                disabled = "installed"
            elif missing:
                disabled = f"requires {', '.join(missing)}"
            else:
                disabled = None
            # A token-list title is emitted verbatim — questionary never applies
            # `highlighted`/`selected` to it — so only disabled rows (skipped by
            # arrow keys, never pointed) may carry styled tokens.
            title: str | list[tuple[str, str]] = (
                [
                    ("class:check", "● " if is_inst else "  "),
                    ("class:text", f"{t.key:<{key_width}}{desc}"),
                ]
                if disabled
                else f"  {t.key:<{key_width}}{desc}"
            )
            choices.append(questionary.Choice(
                title=title,
                value=t.key,
                checked=False,
                disabled=disabled,
            ))

    selected = ui.checkbox(
        "Select packages to install:",
        choices=choices,
        instruction="(Space toggle · Enter confirm · installed items are skipped)",
    )

    if not selected:
        ui.info("No packages selected.")
        return

    tools_to_install = [t for k in selected if (t := registry.get(k)) is not None]
    if not yes and not _confirm_plans(tools_to_install):
        ui.warn("Aborted.")
        return

    results = _install_all(tools_to_install, yes=yes, confirmed=True)
    _report(results)
