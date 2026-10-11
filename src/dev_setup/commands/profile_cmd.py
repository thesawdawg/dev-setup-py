from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, redirect_stdout
from pathlib import Path

import click
from rich.markup import escape

from dev_setup import apply_exec, apply_plan, bundles, profile, registry, snapshot, ui, verbose
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
@click.argument("profile_file", metavar="PROFILE")
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
    profile_file: str, show_all: bool, as_json: bool, exit_code: bool, ignore_extras: bool
) -> None:
    """Compare this machine to a profile.

    PROFILE is a file, or `@name` for a profile that ships with devstuff (e.g. `@devops`;
    `./@x` names a file that starts with an @).

    Read-only. Each key is reported as ok, missing, drift (installed at a different version
    than the profile pins), unverifiable (pinned, but the installed version can't be read),
    unpinnable (pinned, but that install type can't honour a pin), unknown-key (not in this
    machine's catalog) or extra (installed but not in the profile). The last three that mean
    "couldn't compare" are never reported as ok.

    Exit status: 0 whenever the comparison ran. With --exit-code, 1 if the machine differs. A
    profile that can't be read or isn't valid is always 2, so 1 only ever means "differs".
    """
    try:
        wanted = bundles.load_arg(profile_file)
    except profile.ProfileError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    interactive = sys.stdout.isatty() and not as_json
    if interactive:
        ui.print_banner()
    progress = (
        verbose.step(f"Comparing this machine to {Path(profile_file).name}...")
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


def _print_rows(rows: list[DiffRow]) -> None:
    tbl = ui.table()
    tbl.add_column("Package", style="bold", no_wrap=True)
    tbl.add_column("Type", style=ui.CYAN, no_wrap=True)
    tbl.add_column("Profile", style=ui.GRAY, no_wrap=True)
    tbl.add_column("Installed", style=ui.GRAY, no_wrap=True)
    tbl.add_column("Status")
    for r in rows:
        tbl.add_row(r.key, r.type or "", r.pinned or "", r.installed or "", _status_cell(r))
    ui.console.print(tbl)


def _render(rows: list[DiffRow], *, show_all: bool) -> None:
    extras = [r for r in rows if r.state is DiffState.EXTRA]
    shown = rows if show_all else [r for r in rows if r.state not in (DiffState.OK, DiffState.EXTRA)]

    if shown:
        _print_rows(shown)
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


# -- apply --------------------------------------------------------------------------------------

_ERROR_CHARS = 160


def _can_prompt() -> bool:
    """Whether a confirmation could be answered. A seam for tests, which have no terminal."""
    return sys.stdin.isatty()


def _first_line(text: str) -> str:
    """An installer's error, cut to one line: uv and apt can write pages, the summary is a list."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= _ERROR_CHARS else line[: _ERROR_CHARS - 1] + "…"


def _may_ask_for_sudo(tool: object, step: apply_plan.Step) -> bool:
    # Advisory (FR-5), not a control: a guess from the install type and the script's text, used
    # only to put a warning in the plan. A script that reaches sudo another way is not caught,
    # and nothing here prevents or permits anything.
    return step.type == "apt" or "sudo" in (getattr(tool, "install_script", "") or "")


@contextmanager
def _json_safe() -> Iterator[None]:
    """Keep stdout to the one JSON document while installers run (NFR-1).

    Installers print through `ui` (stdout) and, under -v, stream a subprocess's output straight
    to the inherited stdout, where no redirect can reach it. So: stdout is pointed at stderr for
    anything that prints, and streaming is switched off — `-v` has no effect with --json.
    """
    previous = verbose.level()
    verbose.set_level(verbose.QUIET)
    try:
        with redirect_stdout(sys.stderr):
            yield
    finally:
        verbose.set_level(previous)


def _show_plan(
    plan: apply_plan.Plan, by_key: dict[str, object], profile_file: str
) -> None:
    if plan.steps:
        pinned_any = any(s.version for s in plan.steps)
        tbl = ui.table(f"Plan for {Path(profile_file).name}")
        tbl.add_column("Package", style="bold", no_wrap=True)
        tbl.add_column("Type", style=ui.CYAN, no_wrap=True)
        tbl.add_column("Action", no_wrap=True)
        if pinned_any:
            tbl.add_column("Version", style=ui.GRAY, no_wrap=True)
        tbl.add_column("Note", style=ui.GRAY)
        for st in plan.steps:
            notes = []
            if st.needed_by:
                notes.append("needed by " + ", ".join(st.needed_by))
            if _may_ask_for_sudo(by_key.get(st.key), st):
                notes.append("may ask for sudo")
            cells = [st.key, st.type, st.action.value]
            if pinned_any:
                version = st.version or ""
                if st.installed and st.version:
                    version = f"{st.installed} → {st.version}"
                cells.append(version)
            cells.append(escape("; ".join(notes)))
            tbl.add_row(*cells)
        ui.console.print(tbl)
    if plan.skipped:
        ui.dim("Not acted on:")
        for sk in plan.skipped:
            color, label = _LABEL[sk.state]
            ui.console.print(f"  [{color}]{label}[/]  [bold]{sk.key}[/]  [{ui.GRAY}]{escape(sk.reason)}[/]")
    if not plan.steps:
        ui.info("Nothing to install or move.")
    elif plan.ok:
        ui.dim(f"{len(plan.ok)} already match.")


def _progress_hooks(total: int):
    counter = [0]

    def on_start(step: apply_plan.Step) -> None:
        counter[0] += 1
        what = step.action.value + (f" {step.version}" if step.version else "")
        ui.info(f"[{counter[0]}/{total}] {step.key}: {what}")

    def on_done(result: apply_exec.StepResult) -> None:
        S = apply_exec.Status
        if result.status is S.DONE:
            ui.success(f"{result.key}" + (f" {result.version}" if result.version else ""))
        elif result.status is S.FAILED:
            ui.error(f"{result.key} failed: {escape(_first_line(result.detail))}")
        elif result.status is S.SKIPPED:
            ui.warn(f"{result.key} skipped — {escape(result.detail)}")

    return on_start, on_done


def _summarize(
    wanted: profile.Profile,
    run: apply_exec.RunResult | None,
    final: list[DiffRow],
    profile_file: str,
) -> None:
    S = apply_exec.Status
    by_state = {r.key: r.state for r in final}
    ui.console.print()
    if run is not None:
        if run.interrupted:
            ui.warn("Interrupted — the run stopped early. What had finished stays done.")
        for r in run.results:
            if r.status is S.FAILED:
                ui.error(f"{r.key}: {escape(_first_line(r.detail))}")
            elif r.status is S.SKIPPED:
                ui.warn(f"{r.key}: skipped — {escape(r.detail)}")
            elif r.status is S.INTERRUPTED:
                ui.warn(f"{r.key}: interrupted — it may be partly installed")
            elif r.status is S.NOT_RUN:
                ui.dim(f"{r.key}: not run")
            elif by_state.get(r.key) is DiffState.MISSING:
                ui.warn(
                    f"{r.key}: the installer reported success, but the tool isn't detected "
                    "afterwards — for npm and uv tools, is their bin directory on your PATH?"
                )
            elif by_state.get(r.key) is DiffState.UNVERIFIABLE:
                ui.warn(
                    f"{r.key}: installed, version unverified — it can't be read back, "
                    "so the pin is unconfirmed"
                )

    remaining = [r for r in final if r.state not in (DiffState.OK, DiffState.EXTRA)]
    if remaining:
        _print_rows(remaining)
    total = len(wanted.tools)
    matching = sum(1 for r in final if r.state is DiffState.OK)  # only profile keys can be ok
    if matching == total and not remaining:
        ui.success(f"This machine now matches {Path(profile_file).name} ({matching} of {total} entries).")
    else:
        ui.warn(f"{matching} of {total} entries match {Path(profile_file).name}.")


@profile_cmd.command("apply")
@click.argument("profile_file", metavar="PROFILE")
@click.option("--dry-run", is_flag=True, help="Show the plan and exit; change nothing and ask nothing.")
@click.option("--yes", "-y", is_flag=True, help="Don't ask for confirmation.")
@click.option(
    "--missing-only", is_flag=True,
    help="Install what is missing, but leave tools at a different version than the profile pins.",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Write one JSON document to stdout and nothing else (plan, steps, final rows). "
         "Needs --yes or --dry-run; -v has no effect.",
)
def apply_cmd(
    profile_file: str, dry_run: bool, yes: bool, missing_only: bool, as_json: bool
) -> None:
    """Make this machine match a profile.

    PROFILE is a file, or `@name` for a profile that ships with devstuff: `@essentials`, `@python`,
    `@devops`, `@languages`, `@ai-agents`, `@ai-local` (an unknown name lists them).

    Installs the profile's tools that are missing (with whatever they require, in order) and moves
    pinned tools to the pinned version. It never removes anything and never touches a tool the
    profile doesn't pin. The plan is shown first and confirmed — `--yes` skips the question,
    `--dry-run` stops after the plan.

    A failed tool doesn't stop the run: what needed it is skipped and the rest carries on. When it
    finishes, the machine is read again and the result reported from that, not from whether the
    installers returned.

    Exit status: 0 if every entry in the profile now matches; 1 if any doesn't (including
    entries it can't act on, and a run you declined or interrupted); 2 for a profile that can't
    be read, or a run that needs confirming with no terminal to ask on.
    """
    try:
        wanted = bundles.load_arg(profile_file)
    except profile.ProfileError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    if as_json and not (yes or dry_run):
        click.echo("error: --json can't ask for confirmation; add --yes to run, or --dry-run", err=True)
        sys.exit(2)

    tools = registry.all_tools()
    by_key = {t.key: t for t in tools}
    interactive = sys.stdout.isatty() and not as_json
    if interactive:
        ui.print_banner()
    progress = (
        verbose.step(f"Comparing this machine to {Path(profile_file).name}...")
        if interactive else nullcontext()
    )
    with progress:
        facts = snapshot.machine_facts(wanted, tools)
        rows = profile.sort_diff_rows(profile.compare(wanted, facts))
    plan = apply_plan.build_plan(
        rows, facts, lambda key: registry.missing_requires(by_key[key]), missing_only=missing_only
    )

    if not as_json:
        _show_plan(plan, by_key, profile_file)

    if dry_run:
        if as_json:
            click.echo(json.dumps(_document(plan, None, rows), indent=2))
        return

    run: apply_exec.RunResult | None = None
    final = rows  # with nothing to run, the machine is as the first comparison found it
    if not plan.nothing_to_do:
        if not yes:
            if not _can_prompt():
                click.echo(
                    "error: this changes the machine and there is no terminal to confirm on; "
                    "add --yes to proceed, or --dry-run to only see the plan",
                    err=True,
                )
                sys.exit(2)
            n = len(plan.steps)
            if not ui.confirm(f"Apply {n} step{'' if n == 1 else 's'}?"):
                ui.warn("Aborted — nothing was changed.")
                sys.exit(1)
        hooks = (None, None) if as_json else _progress_hooks(len(plan.steps))
        with _json_safe() if as_json else nullcontext():
            run = apply_exec.execute(plan, by_key.get, on_start=hooks[0], on_done=hooks[1])
        # The verdict is a fresh reading of the machine (SD-3), not the steps' own say-so. No
        # cache can be stale here: `perform` forgets every memoised probe after each step.
        final = profile.sort_diff_rows(
            profile.compare(wanted, snapshot.machine_facts(wanted, tools))
        )

    if as_json:
        click.echo(json.dumps(_document(plan, run, final), indent=2))
    else:
        _summarize(wanted, run, final, profile_file)

    if profile.differs(final, ignore_extras=True):
        sys.exit(1)


def _document(
    plan: apply_plan.Plan, run: apply_exec.RunResult | None, rows: list[DiffRow]
) -> dict[str, object]:
    return {
        "plan": plan.to_json(),
        "run": run.to_json() if run is not None else None,
        "rows": [r.to_json() for r in rows],
    }
