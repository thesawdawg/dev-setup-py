from __future__ import annotations

import sys
from pathlib import Path

import click

from dev_setup import profile, snapshot

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
