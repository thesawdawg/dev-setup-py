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
from dev_setup.configure.starship import fonts
from dev_setup.configure.starship import icons as icon_catalog
from dev_setup.configure.starship import preview as live
from dev_setup.configure.starship.model import (
    GROUPS,
    LAYOUTS,
    PALETTES,
    PRESETS,
    SECTIONS,
    SECTIONS_BY_KEY,
    StarshipConfig,
)
from dev_setup.configure.starship.render import sample_markup, to_toml

BASHRC_MARKER = "Starship prompt"  # must match tools.yaml so `remove` still cleans up
BASHRC_LINE = 'eval "$(starship init bash)"'
GENERATED_HEADER = "# Starship prompt configuration"

# ---------------------------------------------------------------------------
# Colour and icon choices for the per-section customizers
# ---------------------------------------------------------------------------

# The 16 ANSI named colours starship accepts. These inherit the terminal's theme,
# so they are the right pick for someone who wants the prompt to track their
# terminal's colour scheme rather than a fixed palette.
_ANSI_COLORS: tuple[tuple[str, str], ...] = (
    ("black", "black"),
    ("red", "red"),
    ("green", "green"),
    ("yellow", "yellow"),
    ("blue", "blue"),
    ("purple", "magenta"),
    ("cyan", "cyan"),
    ("white", "white"),
    ("bright-black", "bright-black"),
    ("bright-red", "bright-red"),
    ("bright-green", "bright-green"),
    ("bright-yellow", "bright-yellow"),
    ("bright-blue", "bright-blue"),
    ("bright-magenta", "bright-magenta"),
    ("bright-cyan", "bright-cyan"),
    ("bright-white", "bright-white"),
)

# A curated spread of hex colours — enough to find something close to any taste
# without overwhelming the list. These are the same families the built-in palettes
# draw from, so they sit well next to them.
_HEX_COLORS: tuple[tuple[str, str], ...] = (
    ("#f38ba8", "Red (soft)"),
    ("#fb4934", "Red (bright)"),
    ("#ff5555", "Red (vivid)"),
    ("#fabd2f", "Yellow (warm)"),
    ("#e0af68", "Yellow (muted)"),
    ("#f9e2af", "Yellow (soft)"),
    ("#a6e3a1", "Green (soft)"),
    ("#b8bb26", "Green (warm)"),
    ("#50fa7b", "Green (vivid)"),
    ("#89b4fa", "Blue (soft)"),
    ("#7aa2f7", "Blue (muted)"),
    ("#1e66f5", "Blue (saturated)"),
    ("#cba6f7", "Purple (soft)"),
    ("#bb9af7", "Purple (muted)"),
    ("#ff79c6", "Pink (vivid)"),
    ("#ebbcba", "Pink (soft)"),
    ("#94e2d5", "Teal (soft)"),
    ("#7dcfff", "Cyan (vivid)"),
    ("#88c0d0", "Cyan (muted)"),
    ("#f5c2e7", "Mauve"),
    ("#f9e2af", "Cream"),
    ("#6c7086", "Grey (dark)"),
    ("#9399b2", "Grey (light)"),
    ("#cdd6f4", "Off-white"),
    ("#1e1e2e", "Dark surface"),
    ("#eff1f5", "Light surface"),
)


def default_config_path() -> Path:
    return Path.home() / ".config" / "starship.toml"


def config_path() -> Path:
    """Where starship reads its config, honouring STARSHIP_CONFIG."""
    override = os.environ.get("STARSHIP_CONFIG")
    return Path(override).expanduser() if override else default_config_path()


# ---------------------------------------------------------------------------
# The five questions
# ---------------------------------------------------------------------------


class _FontGate:
    """Notices that an icon preset has been chosen on a machine with no Nerd Font.

    One probe and at most one offer per wizard run: the style question can be revisited
    from the review menu, and being asked about fonts every time would be a punishment
    for browsing. Declining is remembered too — the answer was "no", not "ask again".
    """

    def __init__(self) -> None:
        self._detected: bool | None = fonts.detect()
        self._offered = False

    def note(self) -> str:
        """The suffix appended to the description of every Nerd Font preset."""
        if self._detected is True:
            return " Needs a Nerd Font — you have one."
        if self._detected is False:
            return " Needs a Nerd Font — none installed here."
        return " Needs a Nerd Font."

    def offer(self) -> None:
        if self._offered or self._detected is not False:
            # None means fontconfig could not tell us. Nagging on a guess would be
            # worse than the icons simply rendering, so stay quiet.
            return
        self._offered = True
        ui.console.print()
        ui.warn("No Nerd Font found — the icons in this style will show as blank boxes.")

        if fonts.is_remote_session():
            # The glyphs are drawn by the terminal emulator, which is on the other end
            # of this connection; a font installed here would never be used.
            ui.dim("  You are over SSH, so the font has to go on the machine your")
            ui.dim(f"  terminal is running on: {fonts.NERD_FONT_URL}")
            return

        from dev_setup import registry
        from dev_setup.commands.install_cmd import install_by_key

        tool = registry.get(fonts.NERD_FONT_KEY)
        if tool is None:  # pragma: no cover — the key is built in
            ui.dim(f"  Install one from {fonts.NERD_FONT_URL}")
            return

        if tool.is_installed():
            # The files are there but fontconfig does not see them, or the terminal is
            # simply pointed at a different font — either way there is nothing to install.
            ui.dim(f"  {tool.name} is already installed.")
            self._remind_to_select(tool.name)
            return

        if not ui.confirm(f"Install {tool.name} now?", default=True):
            ui.dim(f"  You can install one later:  devstuff install {fonts.NERD_FONT_KEY}")
            return

        if install_by_key(fonts.NERD_FONT_KEY):
            self._detected = fonts.detect()
            self._remind_to_select(tool.name)

    def _remind_to_select(self, name: str) -> None:
        """Installing a font does not switch the terminal to it — that is a setting in
        the terminal emulator, and nothing devstuff can do from inside the shell."""
        ui.console.print()
        ui.info(f"Set your terminal's font to '{name}' to see the icons.")
        ui.dim("  It is a terminal preference, not a shell setting — the preview below")
        ui.dim("  will keep showing boxes until you change it.")
        ui.console.print()


def _ask_preset(cfg: StarshipConfig, font: _FontGate | None = None) -> str:
    # Descriptions go in `description` rather than the title: questionary shows them
    # for the highlighted row only, so a long explanation cannot wrap the whole list.
    note = font.note() if font else " Needs a Nerd Font."
    choices = [
        questionary.Choice(
            title=p.label,
            value=p.key,
            description=p.description + (note if p.nerd_font else ""),
        )
        for p in PRESETS.values()
    ]
    chosen = _select("Prompt style:", choices, cfg.preset)
    if font and PRESETS[chosen].nerd_font:
        font.offer()
    return chosen


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


# ---------------------------------------------------------------------------
# Per-section colour and icon customizers
# ---------------------------------------------------------------------------


def _swatch(color: str, text: str, *, pad: int = 2) -> list[tuple[str, str]]:
    """A prompt_toolkit formatted title: a coloured block followed by text.

    The block uses inline ``bg:``/``fg:`` styles so it renders as a real swatch in
    any terminal that supports 256 colours. Hex and ANSI names both work as
    prompt_toolkit colour values."""
    fg = "#1e1e2e" if _is_light(color) else "#eff1f5"
    return [(f"bg:{color} fg:{fg}", " " * pad), ("class:text", f" {text}")]


def _is_light(color: str) -> bool:
    """Rough luminance check for swatch text contrast — light backgrounds get dark
    text, dark ones get light. Good enough for a swatch; not a colour science tool."""
    hex_color = _ANSI_TO_HEX.get(color, color)
    if not hex_color.startswith("#") or len(hex_color) != 7:
        return False
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    # Perceived luminance — the ITU-R BT.601 weighting.
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.5


# Approximate hex values for the 8 basic ANSI colours, for swatch contrast only.
# `bright-*` variants are not mapped here; they default to the dark-text path,
# which is readable on every common terminal theme's bright colours.
_ANSI_TO_HEX: dict[str, str] = {
    "black": "#000000", "red": "#cc0400", "green": "#19b400",
    "yellow": "#cdcd00", "blue": "#0042cc", "purple": "#cc00cc",
    "magenta": "#cc00cc", "cyan": "#00cccc", "white": "#cccccc",
}


def _ask_colors(cfg: StarshipConfig) -> None:
    """The per-section colour customizer. Mutates ``cfg.color_overrides`` in place."""
    selected = cfg.selected()
    if not selected:
        ui.warn("No sections selected — pick sections first.")
        return

    while True:
        # Step 1: pick a section to recolour.
        section_choices: list = []
        for section in selected:
            label = section.label
            if section.key in cfg.color_overrides:
                label += "  (custom)"
            section_choices.append(questionary.Choice(
                title=_swatch(_resolve_swatch_color(cfg, section), label),
                value=section.key,
            ))
        section_choices.append(questionary.Separator())
        section_choices.append(questionary.Choice(title="Done", value="__done__"))
        picked = _select("Section to recolour:", section_choices, "__done__")
        if picked == "__done__" or not picked:
            return

        section = SECTIONS_BY_KEY[picked]
        current = cfg.section_color(section)
        current_label = current if current.startswith("#") else current
        if section.key not in cfg.color_overrides:
            current_label += "  (palette default)"

        # Step 2: pick a colour for that section.
        color_choices: list = [
            questionary.Choice(
                title=_swatch(cfg.color(section.role), f"Reset to palette ({section.role})"),
                value="__reset__",
            ),
            questionary.Separator("\n  ANSI (terminal theme)"),
        ]
        for value, label in _ANSI_COLORS:
            color_choices.append(questionary.Choice(
                title=_swatch(value, label), value=value,
                description=current_label if value == current else "",
            ))
        color_choices.append(questionary.Separator("\n  Hex"))
        for value, label in _HEX_COLORS:
            color_choices.append(questionary.Choice(
                title=_swatch(value, label), value=value,
                description=current_label if value == current else "",
            ))
        color_choices.append(questionary.Separator())
        color_choices.append(questionary.Choice(title="Custom hex…", value="__custom__"))

        chosen = _select(f"Colour for {section.label}:", color_choices, "__reset__")
        if chosen == "__custom__":
            hex_default = current if current.startswith("#") else ""
            hex_input = ui.text_input("Hex colour (e.g. #89b4fa):", default=hex_default)
            if hex_input.strip():
                cfg.color_overrides[section.key] = hex_input.strip()
        elif chosen == "__reset__" or not chosen:
            cfg.color_overrides.pop(section.key, None)
        else:
            cfg.color_overrides[section.key] = chosen


def _resolve_swatch_color(cfg: StarshipConfig, section) -> str:
    """The hex/ANSI colour for a section's swatch — resolves a role name through
    the palette so the swatch matches what the prompt will actually draw."""
    color = cfg.section_color(section)
    if color in cfg.palette_spec.colors:
        color = cfg.color(color)
    return color


def _font_note() -> None:
    """A one-time note about where the font needs to be, shown at the top of the
    icon picker. Over SSH the glyphs are drawn by the client's terminal, so a
    Nerd Font installed here would never be used — the user needs to know that
    before they stare at a list of boxes wondering what went wrong."""
    if fonts.is_remote_session():
        ui.console.print()
        ui.warn("You are over SSH — icons render on the client, not here.")
        ui.dim(f"  Install a Nerd Font on the machine your terminal runs on: {fonts.NERD_FONT_URL}")
        ui.dim("  and set that terminal's font to it. The glyphs below will show as")
        ui.dim("  boxes until then, but the choices still work once the font is in place.")
    elif fonts.detect() is False:
        ui.console.print()
        ui.warn("No Nerd Font found — icons below will show as boxes.")
        ui.dim(f"  Install one:  devstuff install {fonts.NERD_FONT_KEY}   ·   {fonts.NERD_FONT_URL}")
        ui.dim("  The choices still work once the font is installed and your terminal")
        ui.dim("  is set to use it.")
    # detect() is True → say nothing; the picker just works.
    # detect() is None → fontconfig unavailable; guessing wrong would be worse
    # than silence, so stay quiet (mirrors _FontGate's reasoning).


def _ask_icons(cfg: StarshipConfig) -> None:
    """The per-section icon customizer. Mutates ``cfg.icon_overrides`` in place."""
    selected = [s for s in cfg.selected() if s.takes_symbol]
    if not selected:
        ui.warn("No icon-bearing sections selected.")
        return

    _font_note()
    while True:
        # Step 1: pick a section to re-icon.
        section_choices: list = []
        for section in selected:
            glyph = cfg.symbol(section)
            label = section.label
            if section.key in cfg.icon_overrides:
                label += "  (custom)"
            section_choices.append(questionary.Choice(
                title=[("class:text", f"  {glyph} "),
                       ("class:text", label)],
                value=section.key,
            ))
        section_choices.append(questionary.Separator())
        section_choices.append(questionary.Choice(title="Done", value="__done__"))
        picked = _select("Section to re-icon:", section_choices, "__done__")
        if picked == "__done__" or not picked:
            return

        section = SECTIONS_BY_KEY[picked]
        current_glyph = cfg.symbol(section)

        # Step 2: pick an icon — a rendered, grouped select with a search fallback.
        chosen = _pick_icon(section, current_glyph)
        if chosen is None:
            continue  # cancelled the search, back to section picker
        if chosen == "__reset__":
            cfg.icon_overrides.pop(section.key, None)
        else:
            cfg.icon_overrides[section.key] = chosen


def _pick_icon(section, current_glyph: str) -> str | None:
    """The icon picker: a rendered, grouped select with a filterable search fallback.

    Returns the chosen glyph string, ``"__reset__"`` to clear an override, or None
    if the user backed out of both the select and the search."""
    # Find the current icon in the catalog so we can auto-select it.
    current_icon = icon_catalog.find_icon_by_glyph(current_glyph)
    default_key = current_icon.key if current_icon else None

    choices: list = [
        questionary.Choice(
            title=[("class:text", f"  {current_glyph} "),
                   ("class:instruction", f"Reset to default ({section.plain or 'icon'})")],
            value="__reset__",
        ),
        questionary.Separator(),
        questionary.Choice(
            title=[("class:instruction", "Search icons…")],
            value="__search__",
        ),
        questionary.Separator(),
    ]
    for cat in icon_catalog.ICON_CATEGORIES:
        entries = icon_catalog.icons_by_category().get(cat, [])
        if not entries:
            continue
        choices.append(questionary.Separator(f"\n  {cat.upper()}"))
        for icon in entries:
            marker = "  (current)" if icon.key == default_key else ""
            choices.append(questionary.Choice(
                title=[("class:text", f"  {icon.glyph} "),
                       ("class:text", icon.label),
                       ("class:instruction", marker)],
                value=icon.glyph,
            ))
    choices.append(questionary.Separator())
    choices.append(questionary.Choice(
        title=[("class:instruction", "Custom…")],
        value="__custom__",
    ))

    picked = _select(f"Icon for {section.label}:", choices, "__reset__")
    if picked == "__search__":
        return _search_icon(current_glyph)
    if picked == "__custom__":
        custom = ui.text_input("Custom icon text:", default=current_glyph.strip())
        return custom + " " if custom and not custom.endswith(" ") else custom or None
    return picked or "__reset__"


def _search_icon(current_glyph: str) -> str | None:
    """The filterable icon search — autocomplete over the catalog's labels.

    Returns the chosen glyph, or None if the user pressed Enter on empty input
    (meaning "keep the current icon, go back to the section picker")."""
    labels = [icon.label for icon in icon_catalog.ICONS]
    meta = {icon.label: f"{icon.glyph}  ·  {' / '.join(icon.categories)}" for icon in icon_catalog.ICONS}
    result = ui.autocomplete(
        "Search icons (type to filter, Enter to pick):",
        choices=labels,
        meta_information=meta,
    )
    if not result:
        return None  # empty — keep current, back to section picker
    # Map the label back to the glyph; if it doesn't match a known label, treat
    # the typed text as a custom icon.
    for icon in icon_catalog.ICONS:
        if icon.label == result:
            return icon.glyph
    return result


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
    "colors": "Customize section colours",
    "icons": "Customize section icons",
    "layout": "Change the layout",
    "versions": "Toggle language version numbers",
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
    font = _FontGate()

    ui.section("Set up your Starship prompt")
    ui.dim("Pick a look, choose what shows up, and watch the prompt change as you go.")
    ui.dim("Nothing is written until you save. Ctrl-C to bail out at any point.")

    with _sandbox() as box:
        if box is None:
            ui.console.print()
            ui.warn("starship is not installed — previews will be approximate.")
            ui.dim("  Install it with:  devstuff install starship")

        cfg.preset = _ask_preset(cfg, font)
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
                cfg.preset = _ask_preset(cfg, font)
            elif action == "palette":
                cfg.palette = _ask_palette(cfg)
            elif action == "sections":
                cfg.sections = _ask_sections(cfg)
            elif action == "colors":
                _ask_colors(cfg)
            elif action == "icons":
                _ask_icons(cfg)
            elif action == "layout":
                cfg.layout = _ask_layout(cfg)
            elif action == "versions":
                cfg.show_versions = not cfg.show_versions
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
