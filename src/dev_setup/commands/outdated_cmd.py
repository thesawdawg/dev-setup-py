from __future__ import annotations

import json
import sys
from contextlib import nullcontext

import click
from rich.markup import escape

from dev_setup import registry, ui, verbose
from dev_setup.base import Tool
from dev_setup.updates import Row, State, collect_candidates, make_row, sort_rows, summarize

# One glyph + wording per state. `unknown` and `unsupported` deliberately share nothing
# with `current`: "could not check" must never read as "up to date" (spec FR-7).
_STATUS: dict[State, tuple[str, str]] = {
    State.OUTDATED: (ui.AMBER, "⬆ outdated"),
    State.CURRENT: (ui.GREEN, "✔ current"),
    State.UNKNOWN: (ui.GRAY, "? unknown"),
    State.UNSUPPORTED: (ui.GRAY, "– can't check"),
    State.NOT_INSTALLED: (ui.GRAY, "○ not installed"),
}

# Order of the closing summary line; the wording is each state's own name.
_SUMMARY_ORDER = (
    State.OUTDATED, State.CURRENT, State.UNKNOWN, State.UNSUPPORTED, State.NOT_INSTALLED,
)

_FOOTER_NAMES = 12


@click.command("outdated")
@click.option(
    "--updates-only", is_flag=True,
    help="Show only tools with an update available (the summary still counts everything).",
)
@click.option(
    "--all", "show_all", is_flag=True,
    help="List the tools that can't be checked (bash/script installers) instead of one footer line.",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Write a JSON array to stdout and nothing else. Includes every state.",
)
@click.argument("packages", nargs=-1)
def outdated_cmd(packages: tuple[str, ...], updates_only: bool, show_all: bool, as_json: bool) -> None:
    """Show which installed packages have a newer version available.

    Read-only: nothing is installed, updated or written. Each package is reported as
    outdated, current, unknown (a check exists but couldn't answer), or unsupported
    (script/bash installers have no way to check). Unknown and unsupported are never
    reported as current. Use `devstuff update` to act on the result.

    The exit status says whether the lookup ran, not what it found: 0 even when
    updates are available.
    """
    named: list[Tool] | None = None
    if packages:
        named = []
        for key in packages:
            tool = registry.get(key)
            if tool is None:
                click.echo(f"Unknown package: '{key}'", err=True)
                sys.exit(1)
            named.append(tool)

    interactive = sys.stdout.isatty() and not as_json
    if interactive:
        ui.print_banner()
    progress = (
        verbose.step("Checking installed packages for updates...") if interactive else nullcontext()
    )
    with progress:
        candidates = collect_candidates(named)

    result = [make_row(tool, status) for tool, status in candidates]
    if named is not None:
        found = {tool.key for tool, _ in candidates}
        result += [make_row(t, None, installed=False) for t in named if t.key not in found]

    result = sort_rows(result)
    shown = [r for r in result if r.state is State.OUTDATED] if updates_only else result

    if as_json:
        click.echo(json.dumps([r.to_json() for r in shown], indent=2))
        return

    _render(shown, result, updates_only=updates_only, show_all=show_all)


def _render(shown: list[Row], everything: list[Row], *, updates_only: bool, show_all: bool) -> None:
    if not everything:
        ui.info("No installed packages to check.")
        return

    collapsed = [] if show_all else [r for r in shown if r.state is State.UNSUPPORTED]
    rows = [r for r in shown if r not in collapsed]

    if rows:
        tbl = ui.table()
        tbl.add_column("Package", style="bold", no_wrap=True)
        tbl.add_column("Type", style=ui.CYAN, no_wrap=True)
        tbl.add_column("Installed", style=ui.GRAY, no_wrap=True)
        tbl.add_column("Latest", style=ui.GRAY, no_wrap=True)
        tbl.add_column("Status")
        for r in rows:
            tbl.add_row(r.key, r.type, r.installed or "", r.latest or "", _status_cell(r))
        ui.console.print(tbl)
    elif updates_only:
        ui.info("No updates available.")

    if collapsed:
        _footer(collapsed)

    ui.dim(_summary(everything))


def _status_cell(r: Row) -> str:
    color, label = _STATUS[r.state]
    cell = f"[{color}]{label}[/]"
    if r.note:
        # Escaped: a note is free text from a checker, and Rich would read "[docs]" as markup.
        cell += f"\n[{ui.GRAY}]{escape(r.note)}[/]"
    return cell


def _footer(unsupported: list[Row]) -> None:
    kinds = ", ".join(sorted({r.type for r in unsupported}))
    names = [r.key for r in unsupported]
    listed = ", ".join(names[:_FOOTER_NAMES])
    if len(names) > _FOOTER_NAMES:
        listed += f", +{len(names) - _FOOTER_NAMES} more (--all lists them)"
    noun = "tool" if len(names) == 1 else "tools"
    ui.dim(f"{len(names)} {noun} can't be checked ({kinds} installers): {listed}")


def _summary(rows: list[Row]) -> str:
    counts = summarize(rows)
    return " · ".join(
        f"{counts[s]} {s.value.replace('-', ' ')}" for s in _SUMMARY_ORDER if counts[s]
    )
