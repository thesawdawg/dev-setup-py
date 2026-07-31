"""The interactive `devstuff configure commitizen` flow.

Shape: look at the project first, ask the questions its answers cannot supply, then
drop into a review loop where every answer can be revisited against a preview of the
commit types, the bump table and the changelog sections. Nothing touches a file in
the user's project until they pick "Save" (FR-16).
"""

from __future__ import annotations

import re
import shutil
import stat
from datetime import datetime
from pathlib import Path

import questionary
from rich.table import Table

from dev_setup import ui
from dev_setup.configure.commitizen import detect, render, validate
from dev_setup.configure.commitizen.model import (
    BUMP_LEVELS,
    CONVENTIONS,
    NONE,
    SAMPLE_VERSION,
    TAG_FORMATS,
    TARGETS,
    VERSION_PROVIDERS,
    VERSION_SCHEMES,
    ChangeType,
    CommitizenConfig,
)

# A valid commit prefix: what the generated alternations can carry without needing
# to be regex-escaped, and what Conventional Commits itself allows.
TYPE_KEY_RE = re.compile(r"^[a-z][a-z0-9-]*$")

HOOK_BODY = """#!/bin/sh
# Installed by `devstuff configure commitizen`.
cz check --allow-abort --commit-msg-file "$1"
"""


def default_config_path() -> Path:
    project = detect.inspect()
    return project.root / ".cz.toml"


def config_path() -> Path:
    """The file commitizen would actually read here, or where the wizard would put
    one. Mirrors `commitizen.config.read_cfg`: the search order, restricted to files
    that really carry a commitizen section."""
    project = detect.inspect()
    if project.config is not None:
        return project.config
    return project.root / ("pyproject.toml" if project.has_pyproject else ".cz.toml")


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _select(prompt: str, choices: list, current: object) -> str:
    """A select whose cursor starts on the current value, so revisiting a step from
    the review menu shows what is already chosen."""
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or str(current)


def _ask_convention(cfg: CommitizenConfig) -> str:
    choices = [
        questionary.Choice(title=c.label, value=c.key, description=c.description)
        for c in CONVENTIONS.values()
    ]
    return _select("Commit convention:", choices, cfg.convention)


def _ask_types(cfg: CommitizenConfig) -> list[str]:
    catalog = cfg.catalog()
    chosen = set(cfg.types)
    width = max(len(key) for key in catalog) + 2
    choices: list = []
    for key, change_type in catalog.items():
        bump = change_type.bump or "no release"
        choices.append(questionary.Choice(
            title=[
                ("class:text", f"{key:<{width}}"),
                ("class:instruction", f"{bump:<11} {change_type.description}"),
            ],
            value=key,
            checked=key in chosen,
        ))
    selected = ui.checkbox(
        "Commit types to offer:",
        choices=choices,
        instruction="(Space toggle · Enter confirm)",
    )
    if not selected:
        # A convention with no types is a config nobody wants, and commitizen would
        # emit an empty alternation that matches everything.
        ui.warn("No types selected — keeping the previous selection.")
        return cfg.types
    return list(selected)


def _ask_bump_levels(cfg: CommitizenConfig) -> None:
    """Walk the selected types and set what each does to the version.

    Edits `cfg.bumps`/`cfg.in_changelog` in place: they are overrides on the model
    table, so only the types the user actually changed are recorded.
    """
    ui.console.print()
    ui.dim("  For each type: what it does to the version, and where it lands in the")
    ui.dim("  changelog. Enter keeps what is shown.")
    ui.console.print()

    level_choices = [
        questionary.Choice(title=level.label, value=level.key, description=level.description)
        for level in BUMP_LEVELS.values()
    ]
    for change_type in cfg.selected():
        level = _select(f"{change_type.key}: …", level_choices, change_type.bump)
        cfg.bumps[change_type.key] = level
        in_changelog = ui.confirm(
            f"   show {change_type.key} in the changelog?", default=change_type.changelog
        )
        cfg.in_changelog[change_type.key] = in_changelog
        if in_changelog:
            section = ui.text_input("   changelog heading:", default=change_type.section)
            if section:
                cfg.sections[change_type.key] = section

    duplicates = cfg.duplicate_shortcuts()
    if duplicates:
        # commitizen does not validate this; the second type just becomes unreachable
        # by hotkey. Cheap to say, impossible to work out from the file afterwards.
        for shortcut, keys in duplicates.items():
            ui.warn(f"Types {', '.join(keys)} all use shortcut '{shortcut}' — only one wins.")


def _ask_add_type(cfg: CommitizenConfig) -> None:
    key = ui.text_input("New type (lower case, e.g. 'deps'):").strip()
    if not key:
        return
    if not TYPE_KEY_RE.match(key):
        ui.error("A type must start with a letter and use only lower-case letters, digits and '-'.")
        return
    if key in cfg.catalog():
        ui.warn(f"'{key}' already exists — edit it from 'Set what each type bumps'.")
        return

    description = ui.text_input("What is it for?", default=f"{key} changes")
    level_choices = [
        questionary.Choice(title=level.label, value=level.key, description=level.description)
        for level in BUMP_LEVELS.values()
    ]
    bump = _select("What does it do to the version?", level_choices, NONE)
    in_changelog = ui.confirm("Show it in the changelog?", default=bump != NONE)
    section = ui.text_input("Changelog heading:", default=key.title()) if in_changelog else key
    shortcut = ui.text_input("Shortcut key in `cz commit` (one character, optional):").strip()
    if len(shortcut) > 1 or (shortcut and not re.match(r"^[a-z0-9]$", shortcut)):
        ui.warn("Shortcuts must be a single a-z or 0-9 character — skipping it.")
        shortcut = ""

    cfg.extra_types.append(ChangeType(
        key=key,
        section=section or key.title(),
        description=description,
        bump=bump,
        shortcut=shortcut,
        changelog=in_changelog,
        builtin=False,
    ))
    if key not in cfg.types:
        cfg.types.append(key)
    ui.success(f"Added '{key}'.")


def _ask_versioning(cfg: CommitizenConfig, project: detect.Project) -> None:
    provider_choices = [
        questionary.Choice(
            title=p.label
            + ("  (found here)" if p.key == project.provider else ""),
            value=p.key,
            description=p.description,
        )
        for p in VERSION_PROVIDERS.values()
    ]
    cfg.version_provider = _select("Where does the version live?", provider_choices, cfg.version_provider)
    if cfg.provider_spec.needs_version:
        cfg.version = ui.text_input("Current version:", default=cfg.version, required=True)

    scheme_choices = [
        questionary.Choice(title=s.label, value=s.key, description=s.description)
        for s in VERSION_SCHEMES.values()
    ]
    cfg.version_scheme = _select("Version scheme:", scheme_choices, cfg.version_scheme)

    tag_choices = [
        questionary.Choice(title=t.label, value=t.key, description=t.description)
        for t in TAG_FORMATS.values()
    ] + [questionary.Choice(title="Something else…", value="custom")]
    chosen = _select("Git tag format:", tag_choices, cfg.tag_format)
    if chosen == "custom":
        cfg.tag_format = ui.text_input(
            "Tag format ($version, $major, $minor, $patch):",
            default=cfg.tag_format,
            required=True,
        )
    else:
        cfg.tag_format = chosen

    if project.latest_tag:
        expected = validate.tag_for(cfg, SAMPLE_VERSION)
        ui.dim(f"  Latest tag here is {project.latest_tag}; this format would write {expected}.")

    files = ui.text_input(
        "Other files carrying the version (comma-separated, blank for none):",
        default=", ".join(cfg.version_files),
    )
    cfg.version_files = [part.strip() for part in files.split(",") if part.strip()]


_BUMP_TOGGLES = {
    "update_changelog_on_bump": "Regenerate the changelog on every bump",
    "major_version_zero": "0.x mode — a breaking change bumps the minor, not the major",
    "annotated_tag": "Create annotated git tags",
    "gpg_sign": "Sign the bump commit and tag with GPG",
    "changelog_incremental": "Only add new entries to the changelog, keep hand edits",
    "changelog_merge_prerelease": "Fold prerelease entries into the final release",
    "allow_abort": "Reject empty commit messages (`cz check` in CI)",
    "use_shortcuts": "Show hotkeys next to each type in `cz commit`",
}


def _ask_bump_options(cfg: CommitizenConfig) -> None:
    choices = [
        questionary.Choice(title=label, value=key, checked=getattr(cfg, key))
        for key, label in _BUMP_TOGGLES.items()
    ]
    selected = set(ui.checkbox(
        "Release behaviour:", choices=choices, instruction="(Space toggle · Enter confirm)"
    ))
    for key in _BUMP_TOGGLES:
        setattr(cfg, key, key in selected)

    cfg.changelog_file = ui.text_input("Changelog file:", default=cfg.changelog_file)
    cfg.bump_message = ui.text_input(
        "Bump commit message ($current_version, $new_version):",
        default=cfg.bump_message or "",
    )


_PROMPT_TOGGLES = {
    "ask_scope": "Ask for a scope — feat(api): …",
    "ask_body": "Ask for a longer body",
    "ask_footer": "Ask for a footer — issue references, breaking-change details",
}


def _ask_prompt_shape(cfg: CommitizenConfig) -> None:
    choices = [
        questionary.Choice(title=label, value=key, checked=getattr(cfg, key))
        for key, label in _PROMPT_TOGGLES.items()
    ]
    selected = set(ui.checkbox(
        "Questions `cz commit` asks:", choices=choices, instruction="(Space toggle · Enter confirm)"
    ))
    for key in _PROMPT_TOGGLES:
        setattr(cfg, key, key in selected)


def _ask_target(cfg: CommitizenConfig, project: detect.Project) -> str:
    choices = []
    for target in TARGETS.values():
        if target.key == "pyproject.toml" and not project.has_pyproject:
            continue
        choices.append(questionary.Choice(
            title=target.label, value=target.key, description=target.description
        ))
    if len(choices) == 1:
        return str(choices[0].value)
    return _select("Write the configuration to:", choices, cfg.target)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _show_preview(cfg: CommitizenConfig) -> None:
    ui.console.print()
    ui.console.print("  [dim]summary[/]")
    ui.console.print()
    for label, value in render.summary(cfg):
        ui.console.print(f"  [bold cyan]{label:<20}[/] {value}")

    ui.console.print()
    ui.console.print("  [dim]a commit made with[/] [bold]cz commit[/]")
    ui.console.print()
    for line in render.sample_message(cfg).split("\n"):
        ui.console.print(f"    [green]{line}[/]" if line else "")

    ui.console.print()
    ui.console.print(f"  [dim]what each type does to[/] [bold]{SAMPLE_VERSION}[/]")
    ui.console.print()
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("  commit", style="bold")
    table.add_column("increment")
    table.add_column("new version")
    for prefix, level, version in render.bump_rows(cfg):
        style = "dim" if version == SAMPLE_VERSION else "green"
        table.add_row(f"  {prefix}", f"[{style}]{level}[/]", f"[{style}]{version}[/]")
    ui.console.print(table)

    ui.console.print()
    ui.console.print("  [dim]changelog sections[/]")
    ui.console.print()
    for heading, keys in render.changelog_rows(cfg):
        ui.console.print(f"  [bold]{heading:<22}[/] [dim]{', '.join(keys) or '—'}[/]")
    quiet = render.unchangelogged(cfg)
    if quiet:
        ui.console.print()
        ui.dim(f"  Never in the changelog: {', '.join(t.key for t in quiet)}")
    ui.console.print()


def _run_check(cfg: CommitizenConfig, *, quiet_when_ok: bool = False) -> bool:
    """Run the candidate past the real `cz`. Returns False only on a real
    disagreement — a machine without commitizen is not a failure."""
    if not validate.available():
        if not quiet_when_ok:
            ui.console.print()
            ui.warn("commitizen is not installed, so the rules cannot be checked here.")
            ui.dim("  Install it with:  devstuff install commitizen")
        return True

    with ui.spinner("Replaying commits through cz…"):
        report = validate.verify(cfg)
    if report is None:  # pragma: no cover — `available()` was just true
        return True

    if report.ok and quiet_when_ok:
        ui.dim(f"  Checked against cz {report.version} — {len(report.checks)} rules agree.")
        return True

    ui.console.print()
    ui.console.print(f"  [dim]checked with[/] [bold]cz {report.version}[/]")
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        ui.console.print(f"  {mark} {check.name}  [dim]{check.detail}[/]")
    ui.console.print()
    return report.ok


def _show_changelog(cfg: CommitizenConfig) -> None:
    if not validate.available():
        ui.warn("commitizen is not installed — showing the section list instead.")
        for heading, keys in render.changelog_rows(cfg):
            ui.console.print(f"  [bold]{heading}[/] [dim]{', '.join(keys)}[/]")
        return
    with ui.spinner("Generating a changelog from sample commits…"):
        text = validate.changelog_preview(cfg)
    if text is None:
        ui.warn("cz could not generate a changelog from this config.")
        return
    ui.code_block(text, language="markdown")


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


def save(cfg: CommitizenConfig, path: Path) -> tuple[Path, Path | None]:
    """Write the config, backing up whatever was there. Returns (path, backup).

    A `pyproject.toml` is spliced — every `[tool.commitizen…]` table is replaced and
    the rest of the file is left alone. If that splice cannot be verified the write
    falls back to a standalone `.cz.toml` rather than risking the project's own file.
    """
    if path.name == "pyproject.toml" and path.exists():
        spliced = render.splice_pyproject(path.read_text(encoding="utf-8"), cfg)
        if spliced is not None:
            saved_backup = backup(path)
            path.write_text(spliced, encoding="utf-8")
            return path, saved_backup
        ui.warn(f"{path.name} could not be edited safely — writing .cz.toml instead.")
        path = path.with_name(".cz.toml")

    path.parent.mkdir(parents=True, exist_ok=True)
    saved_backup = backup(path)
    path.write_text(render.to_toml(cfg), encoding="utf-8")
    return path, saved_backup


def _looks_generated(path: Path) -> bool:
    """Whether this config came from the wizard. An unreadable file counts as
    hand-written, so the user gets the overwrite warning rather than a traceback."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return text.startswith(render.GENERATED_HEADER) or render.PYPROJECT_MARKER in text


def _warn_about_shadowing(project: detect.Project, written: Path) -> None:
    """commitizen reads exactly one config: the first hit in its search order.

    Writing `.cz.toml` into a project already configured through `pyproject.toml`
    therefore silently disables the old settings — worth saying out loud, since
    nothing in either file hints at the other.
    """
    from dev_setup.configure.commitizen.model import CONFIG_FILES

    others = [p for p in project.configs if p.resolve() != written.resolve()]
    if not others:
        return
    order = {name: i for i, name in enumerate(CONFIG_FILES)}
    ui.console.print()
    ui.warn("More than one commitizen config now exists here:")
    for path in [written, *others]:
        ui.dim(f"    {path.name}")
    winner = min([written, *others], key=lambda p: order.get(p.name, len(CONFIG_FILES)))
    ui.dim(f"  commitizen reads only the first in its search order: {winner.name}")


def _offer_commit_hook(root: Path) -> None:
    """`cz check` as a commit-msg hook — the difference between a convention that is
    documented and one that is enforced. Only offered when there is no hook already:
    replacing someone's existing hook is not a yes/no question."""
    hooks = root / ".git" / "hooks"
    hook = hooks / "commit-msg"
    if not hooks.is_dir() or hook.exists():
        return
    ui.console.print()
    if not ui.confirm("Reject commits that do not match, via a git commit-msg hook?", default=False):
        ui.dim("  You can add it later — see the 'auto check' tutorial in the commitizen docs.")
        return
    try:
        hook.write_text(HOOK_BODY, encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        ui.error(f"Could not write the hook: {exc}")
        return
    ui.success(f"Added {hook}")
    ui.dim("  It runs `cz check`, so `git commit -m 'oops'` now fails.")


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save this configuration",
    "convention": "Change the commit convention",
    "types": "Change which commit types exist",
    "bumps": "Set what each type bumps and where it lands in the changelog",
    "add": "Add a new commit type",
    "versioning": "Change where the version lives, its scheme and tag format",
    "release": "Change release behaviour and the changelog",
    "prompt": "Change the questions `cz commit` asks",
    "target": "Change which file this is written to",
    "check": "Check these rules against the real cz",
    "changelog": "Preview a generated changelog",
    "toml": "Show the generated TOML",
    "cancel": "Cancel without saving",
}

_CUSTOMIZE_ONLY = ("types", "bumps", "add", "prompt")


def _report_project(project: detect.Project) -> None:
    ui.dim(f"Project root: {project.root}")
    if not project.is_git:
        # cz bump reads git history and writes a tag; without a repo it cannot run
        # at all. Worth saying before the questions, not after the file is written.
        ui.warn("This is not a git repository — `cz bump` needs one.")
    if project.configs:
        found = ", ".join(p.name for p in project.configs)
        ui.dim(f"Existing commitizen config: {found}")
    if project.provider != "commitizen":
        ui.dim(f"Version detected: {project.version} (from {project.provider})")
    if project.latest_tag:
        ui.dim(f"Latest git tag: {project.latest_tag}")


def run(*, target: Path | None = None) -> CommitizenConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled.

    `target` overrides where the result is written — `--output` uses it to try a
    config out without touching the project's own file.
    """
    project = detect.inspect()
    cfg = detect.suggest(project)

    ui.section("Set up commitizen")
    ui.dim("Choose your commit types, decide what each one does to the version, and")
    ui.dim("see the result before anything is written. Ctrl-C to bail out at any point.")
    ui.console.print()
    _report_project(project)

    ui.console.print()
    cfg.convention = _ask_convention(cfg)
    if cfg.customizable:
        cfg.types = _ask_types(cfg)
    _ask_versioning(cfg, project)
    _ask_bump_options(cfg)
    if target is None:
        cfg.target = _ask_target(cfg, project)

    while True:
        _show_preview(cfg)
        action = _select(
            "Looks good?",
            [
                questionary.Choice(title=label, value=key)
                for key, label in _MENU.items()
                if cfg.customizable or key not in _CUSTOMIZE_ONLY
            ],
            "save",
        )
        if action == "save":
            break
        if action == "cancel":
            ui.dim("Cancelled — nothing was written.")
            return None
        if action == "toml":
            ui.code_block(render.to_toml(cfg), language="toml")
            continue
        if action == "check":
            _run_check(cfg)
            continue
        if action == "changelog":
            _show_changelog(cfg)
            continue
        if action == "convention":
            cfg.convention = _ask_convention(cfg)
        elif action == "types":
            cfg.types = _ask_types(cfg)
        elif action == "bumps":
            _ask_bump_levels(cfg)
        elif action == "add":
            _ask_add_type(cfg)
        elif action == "versioning":
            _ask_versioning(cfg, project)
        elif action == "release":
            _ask_bump_options(cfg)
        elif action == "prompt":
            _ask_prompt_shape(cfg)
        elif action == "target":
            cfg.target = _ask_target(cfg, project)

    path = target or (project.root / cfg.target)

    # The last thing before writing: if commitizen disagrees with the table the user
    # has been reading, they should hear it while they can still change something.
    if not _run_check(cfg, quiet_when_ok=True) and not ui.confirm(
        "cz does not agree with some of these rules. Save anyway?", default=False
    ):
        ui.dim("Nothing was written.")
        return None

    if path.exists() and not _looks_generated(path) and path.name != "pyproject.toml":
        ui.console.print()
        ui.warn(f"{path} was not written by this wizard — it will be replaced.")
        ui.dim("  A timestamped backup is kept, but any hand-edits move to that backup.")
        if not ui.confirm("Overwrite it?", default=False):
            ui.dim("Cancelled — nothing was written.")
            return None
    if path.name == "pyproject.toml" and detect.read_existing(path):
        ui.console.print()
        ui.warn("pyproject.toml already has a [tool.commitizen] section — it will be replaced.")
        ui.dim("  Everything else in the file is left exactly as it is, and it is backed up.")
        if not ui.confirm("Replace it?", default=True):
            ui.dim("Cancelled — nothing was written.")
            return None

    written, saved_backup = save(cfg, path)
    ui.console.print()
    ui.success(f"Saved {written}")
    if saved_backup:
        ui.dim(f"  Previous version backed up to {saved_backup.name}")

    if target is None:
        _warn_about_shadowing(detect.inspect(), written)
        if project.is_git:
            _offer_commit_hook(project.root)
        ui.console.print()
        ui.dim("Make a commit with:      cz commit")
        ui.dim("Cut a release with:      cz bump")
    else:
        ui.console.print()
        ui.dim(f"Try it without installing it:  cz --config {written} example")
    ui.dim(f"Re-run any time:  devstuff configure commitizen   ·   edit: {written}")
    ui.console.print()
    return cfg


__all__ = ["config_path", "default_config_path", "run", "save"]
