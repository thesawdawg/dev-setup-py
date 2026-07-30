"""The interactive `devstuff configure starship` flow.

Shape: walk the five questions once, previewing after each answer, then drop into a
review loop so any answer can be revisited against a live preview. Nothing touches
the user's real config until they pick "Save" (FR-17).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import questionary
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from dev_setup import base, ui
from dev_setup.configure.starship import preview as live
from dev_setup.configure.starship.model import (
    GROUPS,
    LAYOUTS,
    PALETTES,
    PRESETS,
    SECTIONS,
    StarshipConfig,
)
from dev_setup.configure.starship.render import sample_markup, to_toml

BASHRC_MARKER = "Starship prompt"  # must match tools.yaml so `remove` still cleans up
BASHRC_LINE = 'eval "$(starship init bash)"'
GENERATED_HEADER = "# Starship prompt configuration"


def default_config_path() -> Path:
    return Path.home() / ".config" / "starship.toml"


def config_path() -> Path:
    """Where starship reads its config, honouring STARSHIP_CONFIG."""
    override = os.environ.get("STARSHIP_CONFIG")
    return Path(override).expanduser() if override else default_config_path()


# ---------------------------------------------------------------------------
# The five questions
# ---------------------------------------------------------------------------


def _ask_preset(cfg: StarshipConfig) -> str:
    # Descriptions go in `description` rather than the title: questionary shows them
    # for the highlighted row only, so a long explanation cannot wrap the whole list.
    choices = [
        questionary.Choice(
            title=p.label,
            value=p.key,
            description=p.description + (" Needs a Nerd Font." if p.nerd_font else ""),
        )
        for p in PRESETS.values()
    ]
    return _select("Prompt style:", choices, cfg.preset)


def _ask_palette(cfg: StarshipConfig) -> str:
    choices = [
        questionary.Choice(title=p.label, value=p.key, description=p.description)
        for p in PALETTES.values()
    ]
    return _select("Colour palette:", choices, cfg.palette)


def _ask_layout(cfg: StarshipConfig) -> str:
    choices = [
        questionary.Choice(
            title=layout.label, value=layout.key, description=layout.description
        )
        for layout in LAYOUTS.values()
    ]
    return _select("Layout:", choices, cfg.layout)


def _ask_sections(cfg: StarshipConfig) -> list[str]:
    chosen = set(cfg.sections)
    label_width = max(len(s.label) for s in SECTIONS) + 2
    choices: list = []
    for group in GROUPS:
        entries = [s for s in SECTIONS if s.group == group]
        if not entries:
            continue
        choices.append(questionary.Separator(f"\n  {group.upper()}"))
        for section in entries:
            choices.append(questionary.Choice(
                title=[("class:text", f"{section.label:<{label_width}}"),
                       ("class:instruction", section.key)],
                value=section.key,
                checked=section.key in chosen,
            ))
    selected = ui.checkbox(
        "Sections to show:",
        choices=choices,
        instruction="(Space toggle · Enter confirm)",
    )
    # An empty prompt is a config nobody wants; treat "none" as "leave it alone".
    if not selected:
        ui.warn("No sections selected — keeping the previous selection.")
        return cfg.sections
    return list(selected)


def _select(prompt: str, choices: list, current: str) -> str:
    """A select whose cursor starts on the current value, so revisiting a step from
    the review menu shows what is already chosen."""
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or current


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _show_preview(cfg: StarshipConfig, box: live.Sandbox | None) -> None:
    """Print the prompt as it will look. Real render when starship is installed,
    the offline approximation otherwise (FR-7 / FR-8)."""
    width = min(ui.console.width - 6, 100)
    rendered = live.render(cfg, box, width=width) if box else None

    ui.console.print()
    if rendered is not None:
        ui.console.print("  [dim]preview[/] [dim italic](live — rendered by starship)[/]")
        ui.console.print()
        _print_live(rendered, width)
    else:
        ui.console.print("  [dim]preview[/] [dim italic](approximate — starship not available)[/]")
        ui.console.print()
        for line in sample_markup(cfg, width=width):
            # Crop rather than wrap: a prompt that overflows is information, a prompt
            # reflowed onto three ragged lines is not.
            ui.console.print(f"  {line}", no_wrap=True, crop=True, overflow="ellipsis")
    ui.console.print()
    ui.dim("  Language and cloud sections appear when that project or tool is detected.")
    ui.console.print()


def _print_live(rendered: live.Rendered, width: int) -> None:
    """Print the ANSI starship produced, attaching the right prompt to the line the
    cursor sits on — the last one, which is where a shell's RPROMPT draws it."""
    lines = [Text.from_ansi(line) for line in rendered.left.split("\n")]
    if rendered.right:
        right = Text.from_ansi(rendered.right.strip("\n"))
        # A grid lets Rich measure the cells; padding by hand would have to know the
        # display width of every Nerd Font glyph in the prompt.
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(lines[-1], right)
        for line in lines[:-1]:
            ui.console.print("  ", line, sep="", no_wrap=True, crop=True)
        ui.console.print(Padding(grid, (0, 0, 0, 2)), width=width + 2)
        return
    for line in lines:
        ui.console.print("  ", line, sep="", no_wrap=True, crop=True)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def backup(path: Path) -> Path | None:
    """Copy an existing config aside before overwriting it. Returns the backup path."""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, dest)
    return dest


def save(cfg: StarshipConfig, path: Path | None = None) -> tuple[Path, Path | None]:
    """Write the config, backing up whatever was there. Returns (path, backup)."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    saved_backup = backup(target)
    target.write_text(to_toml(cfg), encoding="utf-8")
    return target, saved_backup


def _looks_generated(path: Path) -> bool:
    """Whether this file came from the wizard. An unreadable file counts as
    hand-written, so the user gets the overwrite warning rather than a traceback."""
    try:
        return path.read_text(errors="replace").startswith(GENERATED_HEADER)
    except OSError:
        return False


def _offer_bashrc_hook() -> None:
    """starship only shows up once the shell hook is in place. The installer adds it,
    so this only fires for someone who configured a manually installed starship."""
    bashrc = Path.home() / ".bashrc"
    if bashrc.exists() and "starship init" in bashrc.read_text():
        return
    ui.console.print()
    ui.warn("~/.bashrc has no starship hook, so the prompt will not load.")
    if ui.confirm(f"Add {BASHRC_LINE} to ~/.bashrc?", default=True) and base.patch_bashrc(
        BASHRC_MARKER, BASHRC_LINE
    ):
        ui.success("Added the starship hook to ~/.bashrc")


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save this configuration",
    "style": "Change the prompt style",
    "palette": "Change the colour palette",
    "sections": "Change which sections show",
    "layout": "Change the layout",
    "spacing": "Toggle the blank line between prompts",
    "toml": "Show the generated TOML",
    "cancel": "Cancel without saving",
}


def run(*, target: Path | None = None) -> StarshipConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled.

    `target` overrides where the result is written — `--output` uses it to try a
    config out without touching the live one.
    """
    cfg = StarshipConfig()

    ui.section("Set up your Starship prompt")
    ui.dim("Pick a look, choose what shows up, and watch the prompt change as you go.")
    ui.dim("Nothing is written until you save. Ctrl-C to bail out at any point.")

    with _sandbox() as box:
        if box is None:
            ui.console.print()
            ui.warn("starship is not installed — previews will be approximate.")
            ui.dim("  Install it with:  devstuff install starship")

        cfg.preset = _ask_preset(cfg)
        _show_preview(cfg, box)
        cfg.palette = _ask_palette(cfg)
        _show_preview(cfg, box)
        cfg.sections = _ask_sections(cfg)
        _show_preview(cfg, box)
        cfg.layout = _ask_layout(cfg)
        cfg.blank_line = ui.confirm("Leave a blank line between prompts?", default=cfg.blank_line)

        while True:
            _show_preview(cfg, box)
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
            if action == "toml":
                ui.code_block(to_toml(cfg), language="toml")
                continue
            if action == "style":
                cfg.preset = _ask_preset(cfg)
            elif action == "palette":
                cfg.palette = _ask_palette(cfg)
            elif action == "sections":
                cfg.sections = _ask_sections(cfg)
            elif action == "layout":
                cfg.layout = _ask_layout(cfg)
            elif action == "spacing":
                cfg.blank_line = not cfg.blank_line

    path = target or config_path()
    if path.exists() and not _looks_generated(path):
        ui.console.print()
        ui.warn(f"{path} was not written by this wizard — it will be replaced.")
        ui.dim("  A timestamped backup is kept, but any hand-edits move to that backup.")
        if not ui.confirm("Overwrite it?", default=False):
            ui.dim("Cancelled — nothing was written.")
            return None

    written, saved_backup = save(cfg, path)
    ui.console.print()
    ui.success(f"Saved {written}")
    if saved_backup:
        ui.dim(f"  Previous config backed up to {saved_backup.name}")

    # Only the file starship actually reads is worth a shell hook or a "restart your
    # shell" nudge; `--output` to a scratch path is just a file on disk.
    if written == config_path():
        _offer_bashrc_hook()
        ui.console.print()
        ui.dim("Open a new shell (or `source ~/.bashrc`) to see it.")
    else:
        ui.console.print()
        ui.dim(f"Try it without installing it:  STARSHIP_CONFIG={written} bash")
    ui.dim(f"Re-run any time:  devstuff configure starship   ·   edit: {written}")
    ui.console.print()
    return cfg


def _sandbox():
    """The live-preview sandbox, or a null context when starship is missing."""
    from contextlib import nullcontext

    if not live.available():
        return nullcontext(None)
    return live.sandbox()
