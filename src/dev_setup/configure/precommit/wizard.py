"""The interactive `devstuff configure pre-commit` flow.

Shape: look at the project first, offer a preset that already matches it, then drop
into a review loop where every answer can be revisited against a preview of the
hooks, when they run, and what they will rewrite. Nothing touches a file in the
user's project until they pick "Save".
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import questionary
from rich.table import Table

from dev_setup import ui
from dev_setup.configure.precommit import detect, render, validate
from dev_setup.configure.precommit.model import (
    CONFIG_FILE,
    GROUPS,
    HOOKS,
    PRESETS,
    REPOS,
    PreCommitConfig,
)

AUTOUPDATE_SCHEDULES = ("weekly", "monthly", "quarterly")


def default_config_path() -> Path:
    return detect.inspect().root / CONFIG_FILE


def config_path() -> Path:
    """The file pre-commit would actually read here.

    Unlike commitizen there is no search order to mirror: pre-commit reads exactly
    `.pre-commit-config.yaml` at the repository root, and `-c` is the only override.
    """
    return default_config_path()


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _select(prompt: str, choices: list, current: object) -> str:
    """A select whose cursor starts on the current value, so revisiting a step from
    the review menu shows what is already chosen."""
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or str(current)


def _ask_preset(cfg: PreCommitConfig, project: detect.Project) -> None:
    detected = detect.detected_hooks(project)
    choices = []
    for preset in PRESETS.values():
        hooks = detected if preset.key == "detected" else preset.hooks
        suffix = ""
        if preset.key == "detected":
            found = ", ".join(project.languages) or "nothing in particular"
            suffix = f"  (found: {found})"
        choices.append(questionary.Choice(
            title=f"{preset.label}{suffix}",
            value=preset.key,
            description=f"{preset.description}  [{len(hooks)} hooks]",
        ))
    chosen = _select("Start from:", choices, cfg.preset)
    cfg.preset = chosen
    cfg.hooks = list(detected if chosen == "detected" else PRESETS[chosen].hooks)


def _ask_hooks(cfg: PreCommitConfig) -> list[str]:
    """The full catalog as one grouped checkbox, pre-ticked with the current set."""
    chosen = set(cfg.hooks)
    width = max(len(hook.key) for hook in HOOKS) + 2
    choices: list = []
    for group in GROUPS.values():
        in_group = [hook for hook in HOOKS if hook.group == group.key]
        if not in_group:
            continue
        choices.append(questionary.Separator(f"  ── {group.label} — {group.description}"))
        for hook in in_group:
            note = "fixes files" if hook.fixes else "reports only"
            if hook.needs:
                note += f" · needs {hook.needs}"
            choices.append(questionary.Choice(
                title=[
                    ("class:text", f"{hook.key:<{width}}"),
                    ("class:instruction", f"{hook.description}  ({note})"),
                ],
                value=hook.key,
                checked=hook.key in chosen,
            ))
    selected = ui.checkbox(
        "Hooks to run:",
        choices=choices,
        instruction="(Space toggle · Enter confirm)",
    )
    if not selected:
        # An empty `repos:` list is valid YAML and a config that does nothing at all,
        # which is never what anyone meant by getting this far.
        ui.warn("No hooks selected — keeping the previous selection.")
        return cfg.hooks
    cfg.preset = "custom" if set(selected) != set(cfg.hooks) else cfg.preset
    return list(selected)


def _ask_args(cfg: PreCommitConfig) -> None:
    """Edit the command-line arguments of the hooks that take any.

    Only hooks the catalog gives default args are offered: those are the ones where a
    number in the default (a size limit, a Python version, an indent width) is a
    project decision rather than a universal one.
    """
    tunable = [hook for hook in cfg.selected() if hook.args]
    if not tunable:
        ui.dim("  None of the selected hooks take arguments.")
        return
    ui.console.print()
    ui.dim("  Space-separated. Enter keeps what is shown; a blank line removes them all.")
    ui.console.print()
    for hook in tunable:
        current = " ".join(hook.args)
        answer = ui.text_input(f"{hook.key}:", default=current)
        new = tuple(answer.split())
        if new == tuple(HOOKS_BY_KEY_ARGS.get(hook.key, ())):
            cfg.args.pop(hook.key, None)  # back to the catalog default; record nothing
        else:
            cfg.args[hook.key] = new


# The catalog's own default args, so `_ask_args` can tell "changed" from "same" and
# only record the difference — the same override discipline as the model tables.
HOOKS_BY_KEY_ARGS = {hook.key: hook.args for hook in HOOKS}


_TOGGLES = {
    "fail_fast": "Stop at the first failing hook instead of running them all",
    "use_ci": "Add a `ci:` block for pre-commit.ci",
    "install_hooks": "Run `pre-commit install` when this is saved",
}


def _ask_behaviour(cfg: PreCommitConfig, project: detect.Project) -> None:
    choices = [
        questionary.Choice(title=label, value=key, checked=getattr(cfg, key))
        for key, label in _TOGGLES.items()
    ]
    selected = set(ui.checkbox(
        "Behaviour:", choices=choices, instruction="(Space toggle · Enter confirm)"
    ))
    for key in _TOGGLES:
        setattr(cfg, key, key in selected)

    if cfg.use_ci:
        schedule_choices = [
            questionary.Choice(title=s, value=s) for s in AUTOUPDATE_SCHEDULES
        ]
        cfg.autoupdate_schedule = _select(
            "How often should pre-commit.ci bump the revs?",
            schedule_choices,
            cfg.autoupdate_schedule,
        )

    if project.exclude and not cfg.exclude:
        ui.dim(f"  Generated files found here: {project.exclude}")
    cfg.exclude = ui.text_input(
        "Paths no hook should touch (a regex, blank for none):",
        default=cfg.exclude,
    )


def _ask_revs(cfg: PreCommitConfig) -> None:
    """Ask the repos themselves what their current tags are.

    The pins in `model.REPOS` were correct when they were written and go stale on
    their own; this is the only thing that knows what is current today.
    """
    if not validate.available():
        ui.warn("pre-commit is not installed, so the revs cannot be refreshed here.")
        ui.dim("  Install it with:  devstuff install pre-commit")
        return
    with ui.spinner("Asking each repository for its latest tag…"):
        found = validate.autoupdate(cfg)
    if found is None:
        ui.warn("Could not reach the repositories — keeping the shipped revs.")
        return

    changed = [
        (key, REPOS[key].rev if key not in cfg.revs else cfg.revs[key], rev)
        for key, rev in found.items()
        if (cfg.revs.get(key) or REPOS[key].rev) != rev
    ]
    if not changed:
        ui.success("Every repository is already pinned to its latest tag.")
        return
    ui.console.print()
    for key, old, new in changed:
        ui.console.print(f"  [bold cyan]{REPOS[key].label:<16}[/] [dim]{old}[/] → [green]{new}[/]")
    ui.console.print()
    # autoupdate takes the newest tag, which is sometimes a prerelease — so this is
    # shown and confirmed rather than applied silently.
    ui.dim("  pre-commit picks the newest tag, which is occasionally a prerelease.")
    if ui.confirm("Use these revs?", default=True):
        cfg.revs.update(found)
        ui.success("Pinned.")


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _show_preview(cfg: PreCommitConfig) -> None:
    ui.console.print()
    ui.console.print("  [dim]summary[/]")
    ui.console.print()
    for label, value in render.summary(cfg):
        ui.console.print(f"  [bold cyan]{label:<22}[/] {value}")

    ui.console.print()
    ui.console.print("  [dim]hooks, in the order they run[/]")
    ui.console.print()
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("  hook", style="bold")
    table.add_column("from")
    table.add_column("stage")
    table.add_column("what it does")
    for key, repo, stage, what in render.hook_rows(cfg):
        table.add_row(f"  {key}", f"[dim]{repo}[/]", f"[dim]{stage}[/]", what)
    ui.console.print(table)

    fixers = cfg.fixers()
    if fixers:
        ui.console.print()
        ui.dim(
            f"  {len(fixers)} of these rewrite files. pre-commit stops the commit when one does,"
        )
        ui.dim("  so the fix lands in the working tree and you re-stage and commit again.")

    prerequisites = cfg.prerequisites()
    if prerequisites:
        ui.console.print()
        ui.console.print("  [dim]needs to be available[/]")
        ui.console.print()
        for need, keys in prerequisites.items():
            ui.console.print(f"  [bold]{need}[/] [dim]— {', '.join(keys)}[/]")

    for warning in cfg.conflicts():
        ui.console.print()
        ui.warn(warning)
    ui.console.print()


def _run_check(cfg: PreCommitConfig, *, quiet_when_ok: bool = False) -> bool:
    """Run the candidate past the real binary. Returns False only on a real
    disagreement — a machine without pre-commit is not a failure."""
    if not validate.available():
        if not quiet_when_ok:
            ui.console.print()
            ui.warn("pre-commit is not installed, so the config cannot be checked here.")
            ui.dim("  Install it with:  devstuff install pre-commit")
        return True

    with ui.spinner("Validating with pre-commit…"):
        report = validate.verify(cfg)
    if report is None:  # pragma: no cover — `available()` was just true
        return True

    if report.ok and quiet_when_ok:
        ui.dim(f"  Checked against pre-commit {report.version} — {len(report.checks)} checks pass.")
        return True

    ui.console.print()
    ui.console.print(f"  [dim]checked with[/] [bold]pre-commit {report.version}[/]")
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        ui.console.print(f"  {mark} {check.name}  [dim]{check.detail}[/]")
    ui.console.print()
    return report.ok


def _run_resolve(cfg: PreCommitConfig) -> None:
    if not validate.available():
        ui.warn("pre-commit is not installed — nothing to resolve against.")
        ui.dim("  Install it with:  devstuff install pre-commit")
        return
    ui.console.print()
    ui.dim("  This clones every repository and builds its environment. First time, that")
    ui.dim("  is minutes, not seconds — but it is the only check that proves each hook id")
    ui.dim("  exists, which `validate-config` never looks at.")
    if not ui.confirm("Go ahead?", default=True):
        return
    with ui.spinner("Cloning repositories and building environments…"):
        report = validate.resolve(cfg)
    if report is None:  # pragma: no cover
        return
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        ui.console.print(f"  {mark} {check.name}  [dim]{check.detail}[/]")
    ui.console.print()


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def backup(path: Path) -> Path | None:
    """Copy an existing config aside before rewriting it. Returns the backup path."""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, dest)
    return dest


def save(cfg: PreCommitConfig, path: Path) -> tuple[Path, Path | None]:
    """Write the config, backing up whatever was there. Returns (path, backup).

    The whole file is ours — unlike commitizen, there is no host file to splice into,
    so this is a plain write and the backup is the entire safety net.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    saved_backup = backup(path)
    path.write_text(render.to_yaml(cfg), encoding="utf-8")
    return path, saved_backup


def _looks_generated(path: Path) -> bool:
    """Whether this config came from the wizard. An unreadable file counts as
    hand-written, so the user gets the overwrite warning rather than a traceback."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return text.startswith(render.GENERATED_HEADER)


def _offer_install(cfg: PreCommitConfig, project: detect.Project) -> None:
    """`pre-commit install` — the difference between a config that exists and hooks
    that actually run. A `.pre-commit-config.yaml` on its own does nothing at all."""
    if not project.is_git:
        ui.warn("This is not a git repository, so there is nowhere to install the hooks.")
        return
    if not validate.available():
        ui.console.print()
        ui.warn("pre-commit is not installed, so the git hooks were not set up.")
        ui.dim("  devstuff install pre-commit  &&  pre-commit install")
        return
    hook_types = cfg.install_hook_types()
    ok, message = validate.install(project.root, hook_types)
    if ok:
        ui.success(f"Installed git hooks: {', '.join(hook_types)}")
    else:
        ui.error(f"Could not install the git hooks: {message}")
        ui.dim(f"  Try by hand:  pre-commit install --install-hook-types {','.join(hook_types)}")


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save this configuration",
    "preset": "Start from a different preset",
    "hooks": "Change which hooks run",
    "args": "Change a hook's arguments",
    "behaviour": "Change behaviour, excludes and the pre-commit.ci block",
    "revs": "Refresh every repository to its latest tag",
    "check": "Validate this against the real pre-commit",
    "resolve": "Clone the repos and prove every hook id exists (slow)",
    "yaml": "Show the generated YAML",
    "cancel": "Cancel without saving",
}


def _report_project(project: detect.Project) -> None:
    ui.dim(f"Project root: {project.root}")
    if not project.is_git:
        # pre-commit's hooks are git hooks; without a repo it cannot install anything.
        ui.warn("This is not a git repository — pre-commit needs one to install hooks into.")
    if project.languages:
        found = ", ".join(f"{name} ({count})" for name, count in project.languages.items())
        ui.dim(f"Languages found: {found}")
    elif project.file_count:
        ui.dim(f"{project.file_count} files, no language this wizard has hooks for.")
    if project.config:
        ui.dim(f"Existing config: {project.config.name} ({len(project.existing_hooks)} hooks)")
    if project.legacy_config:
        # pre-commit does not read the .yml spelling at all, so a repo carrying one
        # believes it is configured and is not.
        ui.warn(f"{project.legacy_config.name} exists, but pre-commit only reads {CONFIG_FILE}.")
    if project.installed_hook_types:
        ui.dim(f"Git hooks already installed: {', '.join(project.installed_hook_types)}")
    elif project.config:
        ui.warn("A config exists but `pre-commit install` has not run — the hooks never fire.")
    if project.has_commitizen:
        ui.dim("commitizen config found — the commit-msg hook for it is available.")


def run(*, target: Path | None = None) -> PreCommitConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled.

    `target` overrides where the result is written — `--output` uses it to try a
    config out without touching the project's own file.
    """
    project = detect.inspect()
    cfg = detect.suggest(project)

    ui.section("Set up pre-commit")
    ui.dim("Pick a starting set of hooks, adjust it, and see exactly what will run and")
    ui.dim("what it will rewrite before anything is written. Ctrl-C to bail out.")
    ui.console.print()
    _report_project(project)

    ui.console.print()
    _ask_preset(cfg, project)
    cfg.hooks = _ask_hooks(cfg)
    _ask_behaviour(cfg, project)

    while True:
        _show_preview(cfg)
        action = _select(
            "Looks good?",
            [questionary.Choice(title=label, value=key) for key, label in _MENU.items()],
            "save",
        )
        if action == "save":
            break
        if action == "cancel":
            ui.dim("Cancelled — nothing was written.")
            return None
        if action == "yaml":
            ui.code_block(render.to_yaml(cfg), language="yaml")
            continue
        if action == "check":
            _run_check(cfg)
            continue
        if action == "resolve":
            _run_resolve(cfg)
            continue
        if action == "preset":
            _ask_preset(cfg, project)
        elif action == "hooks":
            cfg.hooks = _ask_hooks(cfg)
        elif action == "args":
            _ask_args(cfg)
        elif action == "behaviour":
            _ask_behaviour(cfg, project)
        elif action == "revs":
            _ask_revs(cfg)

    path = target or (project.root / cfg.target)

    # The last thing before writing: if pre-commit disagrees with what the user has
    # been reading, they should hear it while they can still change something.
    if not _run_check(cfg, quiet_when_ok=True) and not ui.confirm(
        "pre-commit is not happy with this config. Save anyway?", default=False
    ):
        ui.dim("Nothing was written.")
        return None

    if path.exists() and not _looks_generated(path):
        ui.console.print()
        ui.warn(f"{path} was not written by this wizard — it will be replaced.")
        if project.existing_hooks:
            ui.dim(f"  It currently runs: {', '.join(sorted(set(project.existing_hooks)))}")
        ui.dim("  A timestamped backup is kept, but any hand-edits move to that backup.")
        if not ui.confirm("Overwrite it?", default=False):
            ui.dim("Cancelled — nothing was written.")
            return None

    written, saved_backup = save(cfg, path)
    ui.console.print()
    ui.success(f"Saved {written}")
    if saved_backup:
        ui.dim(f"  Previous version backed up to {saved_backup.name}")

    if target is None:
        if cfg.install_hooks:
            _offer_install(cfg, project)
        else:
            ui.dim(
                "  The hooks are not installed yet:  pre-commit install"
                f" --install-hook-types {','.join(cfg.install_hook_types())}"
            )
        ui.console.print()
        ui.dim("Check everything now:     pre-commit run --all-files")
        ui.dim("Bump every rev later:     pre-commit autoupdate")
    else:
        ui.console.print()
        ui.dim(f"Try it without installing it:  pre-commit run --all-files -c {written}")
    ui.dim(f"Re-run any time:  devstuff configure pre-commit   ·   edit: {written}")
    ui.console.print()
    return cfg


__all__ = ["config_path", "default_config_path", "run", "save"]
