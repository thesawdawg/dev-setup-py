"""The interactive `devstuff configure lazygit` flow.

Two things worth knowing about the shape:

- The icon settings are gated on the Nerd Font check, reusing the starship
  configurator's `fonts` module rather than adding a second one. Turning icons on
  without the font is the most common way a lazygit config looks broken.
- "Start lazygit against this config" is an explicit menu action, not a save-time
  check, because it needs a pty and a few seconds.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import questionary
from rich.table import Table

from dev_setup import ui
from dev_setup.configure.lazygit import detect, render, validate
from dev_setup.configure.lazygit.model import (
    PAGERS,
    PRESETS,
    SETTINGS,
    LazygitConfig,
)


def config_path() -> Path:
    """Where lazygit says its config lives — asked of the binary."""
    return detect.inspect().path


def _select(prompt: str, choices: list, current: object) -> str:
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or str(current)


def _ask_preset(cfg: LazygitConfig, found: detect.Lazygit) -> None:
    choices = [
        questionary.Choice(title=preset.label, value=preset.key, description=preset.description)
        for preset in PRESETS.values()
        if not (preset.key == "current" and not found.has())
    ]
    detect.apply_preset(cfg, _select("Start from:", choices, cfg.preset), found)


def _ask_icons(cfg: LazygitConfig, found: detect.Lazygit) -> None:
    """Icons, gated on whether the font that draws them is actually installed."""
    if found.nerd_font is False:
        ui.console.print()
        ui.warn("No Nerd Font found, so icons would render as boxes and question marks.")
        ui.dim("  Install one with:  devstuff install nerd-font")
        ui.dim("  Installing a font does not repoint your terminal at it — that is a")
        ui.dim("  terminal setting you have to change yourself.")
        if not ui.confirm("Turn icons on anyway?", default=False):
            cfg.nerd_fonts_version = ""
            cfg.show_icons = False
            return
    elif found.nerd_font is None:
        ui.dim("  Could not tell whether a Nerd Font is installed — assuming it is.")

    setting = SETTINGS["nerd_fonts_version"]
    cfg.nerd_fonts_version = _select(
        "Nerd Font icons:",
        [
            questionary.Choice(title="off", value="", description="No icons at all"),
            questionary.Choice(
                title="version 3", value="3", description="Current Nerd Fonts (v3 and later)"
            ),
            questionary.Choice(
                title="version 2", value="2", description="Older Nerd Fonts (pre-v3 glyphs)"
            ),
        ],
        cfg.nerd_fonts_version if cfg.nerd_fonts_version in setting.choices else "",
    )
    cfg.show_icons = bool(cfg.nerd_fonts_version) and ui.confirm(
        "Show a file-type icon beside each filename?", default=cfg.show_icons
    )


def _ask_pager(cfg: LazygitConfig) -> None:
    ui.console.print()
    ui.dim("  An external pager renders diffs. delta is the usual choice; it must be")
    ui.dim("  installed separately and needs --paging=never so it does not open its own.")
    choices = [
        questionary.Choice(title=label, value=command) for command, label in PAGERS.items()
    ]
    chosen = _select("Diff pager:", choices, cfg.pager)
    if chosen == "custom":  # pragma: no cover — reserved for a future free-text option
        chosen = ui.text_input("Pager command:", default=cfg.pager)
    cfg.pager = chosen
    if cfg.pager:
        tool = cfg.pager.split()[0]
        if shutil.which(tool) is None:
            ui.warn(f"{tool} is not on your PATH — diffs would fail to render until it is.")
        cfg.color_arg = "always"


def _ask_appearance(cfg: LazygitConfig) -> None:
    toggles = {
        "show_file_tree": "Show changed files as a tree",
        "show_command_log": "Show the command log panel",
        "show_bottom_line": "Show the bottom information line",
        "show_random_tip": "Show random tips",
        "mouse_events": "Respond to the mouse",
    }
    selected = set(ui.checkbox(
        "Interface:",
        choices=[
            questionary.Choice(
                title=label,
                value=key,
                checked=bool(getattr(cfg, key)),
                description=SETTINGS[key].why or SETTINGS[key].description,
            )
            for key, label in toggles.items()
        ],
        instruction="(Space toggle · Enter confirm)",
    ))
    for key in toggles:
        setattr(cfg, key, key in selected)


def _ask_git(cfg: LazygitConfig) -> None:
    toggles = {
        "auto_fetch": "Fetch in the background",
        "fetch_all": "Fetch every remote, not just origin",
        "sign_off": "Add Signed-off-by to commits",
        "disable_force_pushing": "Remove force push from the interface",
        "ignore_whitespace": "Ignore whitespace-only changes in diffs",
    }
    selected = set(ui.checkbox(
        "Git behaviour:",
        choices=[
            questionary.Choice(
                title=label,
                value=key,
                checked=bool(getattr(cfg, key)),
                description=SETTINGS[key].why or SETTINGS[key].description,
            )
            for key, label in toggles.items()
        ],
        instruction="(Space toggle · Enter confirm)",
    ))
    for key in toggles:
        setattr(cfg, key, key in selected)

    cfg.log_show_graph = _select(
        "Draw the commit graph:",
        [questionary.Choice(title=c, value=c) for c in SETTINGS["log_show_graph"].choices],
        cfg.log_show_graph,
    )
    cfg.log_order = _select(
        "Commit ordering:",
        [questionary.Choice(title=c, value=c) for c in SETTINGS["log_order"].choices],
        cfg.log_order,
    )


def _ask_editor(cfg: LazygitConfig) -> None:
    choices = [
        questionary.Choice(
            title=c or "leave it to lazygit ($EDITOR)",
            value=c,
        )
        for c in SETTINGS["edit_preset"].choices
    ]
    cfg.edit_preset = _select("Editor to open on `e`:", choices, cfg.edit_preset)
    if cfg.edit_preset and shutil.which(cfg.edit_preset) is None:
        ui.warn(f"{cfg.edit_preset} is not on your PATH.")


def _ask_prompts(cfg: LazygitConfig) -> None:
    toggles = {
        "confirm_on_quit": "Ask before quitting",
        "quit_on_top_level_return": "Escape at the top level quits",
        "disable_startup_popups": "Skip the startup popups",
    }
    selected = set(ui.checkbox(
        "Prompts:",
        choices=[
            questionary.Choice(title=label, value=key, checked=bool(getattr(cfg, key)))
            for key, label in toggles.items()
        ],
        instruction="(Space toggle · Enter confirm)",
    ))
    for key in toggles:
        setattr(cfg, key, key in selected)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _show_preview(cfg: LazygitConfig, found: detect.Lazygit) -> None:
    ui.console.print()
    for label, value in render.summary(cfg):
        ui.console.print(f"  [bold cyan]{label:<16}[/] {value}")

    rows = render.setting_rows(cfg)
    ui.console.print()
    if not rows:
        ui.dim("  Nothing is set — this writes an empty config and every lazygit default applies.")
        ui.console.print()
        return

    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("  area")
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_column("note")
    for group, path, value, why in rows:
        table.add_row(f"  [dim]{group}[/]", path, value, f"[dim]{why}[/]")
    ui.console.print(table)

    for warning in cfg.warnings():
        ui.console.print()
        ui.warn(warning)
    for path, why in detect.retired_keys(cfg):
        ui.console.print()
        ui.warn(f"{path}: {why}")
    if cfg.wants_icons() and found.nerd_font is False:
        ui.console.print()
        ui.warn("Icons are on but no Nerd Font was found.")
    ui.console.print()


def _run_check(cfg: LazygitConfig, found: detect.Lazygit, *, quiet_when_ok: bool = False) -> bool:
    report = validate.verify(cfg, found)
    if report.ok and quiet_when_ok:
        ui.dim(f"  {len(report.checks)} checks pass.")
        return True
    ui.console.print()
    ui.console.print(f"  [dim]checked against[/] [bold]lazygit {report.version or 'unknown'}[/]")
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        ui.console.print(f"  {mark} {check.name}  [dim]{check.detail}[/]")
    ui.console.print()
    return report.ok


def _run_launch(cfg: LazygitConfig) -> None:
    """Start the real lazygit against the candidate — the only check lazygit does."""
    if not validate.available():
        ui.warn("lazygit is not installed, so it cannot be started against this config.")
        ui.dim("  Install it with:  devstuff install lazygit")
        return
    ui.console.print()
    ui.dim("  This starts lazygit in a throwaway repository and quits it again. It is")
    ui.dim("  the only thing that proves lazygit will load the file — though note that")
    ui.dim("  lazygit ignores unknown keys, so starting is not proof they all work.")
    with ui.spinner("Starting lazygit against the candidate…"):
        result = validate.launch(cfg)
    if result is None:  # pragma: no cover
        return
    started, message = result
    ui.console.print()
    if started:
        ui.success(message)
    else:
        ui.error(message)
    ui.console.print()


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak.{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, dest)
    return dest


def save(cfg: LazygitConfig, path: Path) -> tuple[Path, Path | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = backup(path)
    path.write_text(render.to_yaml(cfg), encoding="utf-8")
    return path, saved


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save this configuration",
    "preset": "Start from a different preset",
    "icons": "Change the Nerd Font icons",
    "pager": "Change the diff pager",
    "appearance": "Change which panels are shown",
    "git": "Change git behaviour and the commit graph",
    "editor": "Change the editor",
    "prompts": "Change the confirmation prompts",
    "check": "Check this configuration",
    "launch": "Start lazygit against it (slow)",
    "yaml": "Show the generated config.yml",
    "cancel": "Cancel without saving",
}


def _report(found: detect.Lazygit) -> None:
    if not found.installed:
        ui.warn("lazygit is not installed — the wizard can still write a config.")
        ui.dim("  Install it with:  devstuff install lazygit")
    else:
        ui.dim(f"lazygit {found.version} · config dir {found.config_dir}")

    if found.has():
        ui.dim(f"Existing config: {found.path.name}"
               + (" (written by this wizard)" if found.generated else ""))
        if not found.parse_ok:
            # lazygit refuses to start on unparseable YAML, so this is not subtle —
            # but it does mean the user is currently unable to run lazygit at all.
            ui.warn("The existing config does not parse — lazygit will not start with it.")
        extras = [key for key in found.existing if key in ("customCommands", "keybinding")]
        if extras:
            ui.dim(f"It also carries {', '.join(extras)} — those are preserved untouched.")
    else:
        ui.dim(f"No config at {found.path} yet.")

    if found.nerd_font is False:
        ui.dim("No Nerd Font found, so icons are off by default.")
    # `None` means the font could not be checked, and the gate stays silent rather
    # than warning about something it cannot see.


def run(*, target: Path | None = None) -> LazygitConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled."""
    found = detect.inspect()
    cfg = detect.suggest(found)
    if target is not None:
        cfg.target = target

    ui.section("Configure lazygit")
    ui.dim("Pick a starting point and adjust it. Nothing is written until you save.")
    ui.dim("Ctrl-C to bail out.")
    ui.console.print()
    _report(found)

    ui.console.print()
    _ask_preset(cfg, found)
    _ask_icons(cfg, found)

    while True:
        _show_preview(cfg, found)
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
            _run_check(cfg, found)
            continue
        if action == "launch":
            _run_launch(cfg)
            continue
        if action == "preset":
            _ask_preset(cfg, found)
            _ask_icons(cfg, found)
        elif action == "icons":
            _ask_icons(cfg, found)
        elif action == "pager":
            _ask_pager(cfg)
        elif action == "appearance":
            _ask_appearance(cfg)
        elif action == "git":
            _ask_git(cfg)
        elif action == "editor":
            _ask_editor(cfg)
        elif action == "prompts":
            _ask_prompts(cfg)

    path = target or cfg.target

    if not _run_check(cfg, found, quiet_when_ok=True) and not ui.confirm(
        "Some checks failed. Save anyway?", default=False
    ):
        ui.dim("Nothing was written.")
        return None

    if path.exists() and not found.generated and target is None:
        ui.console.print()
        ui.warn(f"{path} was not written by this wizard — it will be replaced.")
        lines = render.diff(found.existing_text, render.to_yaml(cfg))
        if lines:
            ui.code_block("\n".join(lines), language="diff")
        ui.dim("  A timestamped backup is kept.")
        if not ui.confirm("Overwrite it?", default=False):
            ui.dim("Cancelled — nothing was written.")
            return None

    written, saved_backup = save(cfg, path)
    ui.console.print()
    ui.success(f"Saved {written}")
    if saved_backup:
        ui.dim(f"  Previous version backed up to {saved_backup.name}")

    ui.console.print()
    ui.dim("See every available option:  lazygit --config")
    ui.dim(f"Re-run any time:  devstuff configure lazygit   ·   edit: {written}")
    ui.console.print()
    return cfg


__all__ = ["backup", "config_path", "run", "save"]
