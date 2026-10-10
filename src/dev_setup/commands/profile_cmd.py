from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from pathlib import Path

import click
from rich.markup import escape

from dev_setup import profile, snapshot, ui, verbose
from dev_setup.profile import DiffRow, DiffState

_CUSTOM_NAMES_SHOWN = 6


def _warn(message: str) -> None:
    # stderr only: with no -o, stdout is the profile itself and must parse (spec FR-14, NFR-1).
    click.echo(message, err=True)


@click.group("profile")
def profile_cmd() -> None:
    """Describe a machine's tools as a portable file, and compare a machine against one.

    A profile is a small YAML file naming catalog tools (and, optionally, versions). It holds
    keys only — never install scripts — so a profile from elsewhere cannot run anything the
    catalog doesn't already define.
    """


@profile_cmd.command("snapshot")
@click.option(
    "-o", "--output", "output", type=click.Path(path_type=Path), default=None,
    help="Write to this file instead of stdout.",
)
@click.option(
    "--versions", is_flag=True,
    help="Also record each tool's installed version, for the types that can be pinned "
         "(npm, pip/uvx, single-package apt). Off by default: a pinned profile goes stale "
         "the day a tool updates.",
)
@click.option("--force", is_flag=True, help="Overwrite the -o file if it already exists.")
def snapshot_cmd(output: Path | None, versions: bool, force: bool) -> None:
    """Write a profile of the tools installed on this machine.

    Read-only, and local: it asks the machine, never the network, so it works offline. The
    output is deterministic (sorted, no timestamp, no hostname), so a profile kept in git shows
    only real changes.

    With no -o, stdout is exactly the YAML — notes and warnings go to stderr.
    """
    if output is not None and output.exists() and not force:
        _warn(f"error: {output} already exists; use --force to overwrite it")
        sys.exit(2)

    snap = snapshot.take(versions=versions)

    for key in snap.unreadable:
        _warn(
            f"warning: {key}: couldn't read its installed version, so it is written without "
            f"one — {snap.why[key]}"
        )
    if snap.custom:
        shown = ", ".join(snap.custom[:_CUSTOM_NAMES_SHOWN])
        if len(snap.custom) > _CUSTOM_NAMES_SHOWN:
            shown += f", +{len(snap.custom) - _CUSTOM_NAMES_SHOWN} more"
        _warn(
            f"note: {len(snap.custom)} of these tools are defined or overridden in your user "
            f"catalog ({shown}); a machine without them won't know these keys — move them with "
            "`devstuff catalog export` / `devstuff catalog import`"
        )

    text = profile.dumps(snap.profile)
    if output is None:
        click.echo(text, nl=False)
        return

    try:
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        _warn(f"error: cannot write {output}: {exc.strerror or exc}")
        sys.exit(2)
    n = len(snap.profile.tools)
    _warn(f"Wrote {n} tool{'' if n == 1 else 's'} to {output}")


# -- diff ---------------------------------------------------------------------------------------

# One glyph + wording per state. The three "could not compare" states share nothing with `ok`:
# they must never read as a match (spec FR-17).
_LABEL: dict[DiffState, tuple[str, str]] = {
    DiffState.OK: (ui.GREEN, "✔ ok"),
    DiffState.MISSING: (ui.RED, "✘ missing"),
    DiffState.DRIFT: (ui.AMBER, "≠ drift"),
    DiffState.UNVERIFIABLE: (ui.GRAY, "? unverifiable"),
    DiffState.UNPINNABLE: (ui.GRAY, "– unpinnable"),
    DiffState.UNKNOWN_KEY: (ui.GRAY, "? unknown-key"),
    DiffState.EXTRA: (ui.CYAN, "+ extra"),
}

_EXTRA_NAMES_SHOWN = 12


@profile_cmd.command("diff")
@click.argument("profile_file", metavar="PROFILE", type=click.Path(path_type=Path))
@click.option(
    "--all", "show_all", is_flag=True,
    help="Also list the tools that match, and the tools not in the profile, as rows.",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Write a JSON array to stdout and nothing else. Always includes every state.",
)
@click.option(
    "--exit-code", "exit_code", is_flag=True,
    help="Exit 1 if the machine differs from the profile (default: 0 whenever the comparison ran).",
)
@click.option(
    "--ignore-extras", is_flag=True,
    help="For --exit-code: don't count installed tools that aren't in the profile as a difference.",
)
def diff_cmd(
    profile_file: Path, show_all: bool, as_json: bool, exit_code: bool, ignore_extras: bool
) -> None:
    """Compare this machine to a profile.

    Read-only. Each key is reported as ok, missing, drift (installed at a different version
    than the profile pins), unverifiable (pinned, but the installed version can't be read),
    unpinnable (pinned, but that install type can't honour a pin), unknown-key (not in this
    machine's catalog) or extra (installed but not in the profile). The last three that mean
    "couldn't compare" are never reported as ok.

    Exit status: 0 whenever the comparison ran. With --exit-code, 1 if the machine differs. A
    profile that can't be read or isn't valid is always 2, so 1 only ever means "differs".
    """
    try:
        wanted = profile.load(profile_file)
    except profile.ProfileError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    interactive = sys.stdout.isatty() and not as_json
    if interactive:
        ui.print_banner()
    progress = (
        verbose.step(f"Comparing this machine to {profile_file.name}...")
        if interactive else nullcontext()
    )
    with progress:
        rows = profile.sort_diff_rows(profile.compare(wanted, snapshot.machine_facts(wanted)))

    if as_json:
        click.echo(json.dumps([r.to_json() for r in rows], indent=2))
    else:
        _render(rows, show_all=show_all)

    if exit_code and profile.differs(rows, ignore_extras=ignore_extras):
        sys.exit(1)


def _status_cell(row: DiffRow) -> str:
    color, label = _LABEL[row.state]
    cell = f"[{color}]{label}[/]"
    if row.note:
        # Escaped: a note is free text, and Rich would read "[docs]" as markup.
        cell += f"\n[{ui.GRAY}]{escape(row.note)}[/]"
    return cell


def _render(rows: list[DiffRow], *, show_all: bool) -> None:
    extras = [r for r in rows if r.state is DiffState.EXTRA]
    shown = rows if show_all else [r for r in rows if r.state not in (DiffState.OK, DiffState.EXTRA)]

    if shown:
        tbl = ui.table()
        tbl.add_column("Package", style="bold", no_wrap=True)
        tbl.add_column("Type", style=ui.CYAN, no_wrap=True)
        tbl.add_column("Profile", style=ui.GRAY, no_wrap=True)
        tbl.add_column("Installed", style=ui.GRAY, no_wrap=True)
        tbl.add_column("Status")
        for r in shown:
            tbl.add_row(r.key, r.type or "", r.pinned or "", r.installed or "", _status_cell(r))
        ui.console.print(tbl)
    elif not extras:
        ui.info("This machine matches the profile.")
    else:
        ui.info("Everything the profile asks for is here.")

    if extras and not show_all:
        names = [r.key for r in extras]
        listed = ", ".join(names[:_EXTRA_NAMES_SHOWN])
        if len(names) > _EXTRA_NAMES_SHOWN:
            listed += f", +{len(names) - _EXTRA_NAMES_SHOWN} more"
        noun = "tool isn't" if len(names) == 1 else "tools aren't"
        ui.dim(f"{len(names)} installed {noun} in the profile: {listed}  (--all lists them)")

    counts = profile.summarize_diff(rows)
    ui.dim(" · ".join(f"{counts[s]} {s.value}" for s in profile.DIFF_ORDER if counts[s]))
