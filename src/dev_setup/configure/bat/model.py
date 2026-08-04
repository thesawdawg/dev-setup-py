"""The data behind the bat wizard. Everything else in the package reads these tables.

`THEMES`, `COMPONENTS`, `SETTINGS` and `PRESETS` are ordered — declaration order is
prompt order. Adding a theme or a component is one record.

**On the theme table.** The authoritative list of themes is whatever
`bat --list-themes` says on *this* machine — a user can add their own through
`bat cache --build`, and the shipped list would then be wrong. So `detect.py`
enumerates at run time and this table is the fallback plus the *metadata* bat does
not expose: whether a theme is meant for a light or a dark terminal.

That metadata was measured rather than recalled — every theme was rendered and the
mean luminance of its foreground colours taken, which classifies a light theme
(dark text) from a dark one (light text). It got 24 of 28 right and was wrong in a
way worth recording: **the Solarized pair is unclassifiable this way**, because
Solarized's light and dark variants share one palette by design and both land at
luminance ~130. Three more (`ansi`, `base16`, `base16-256`) emit no true colour at
all — they follow the terminal's own 16 colours, so they are neither light nor dark.
Hence `Theme.mode` has three values and was hand-corrected against the measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "config"

# bat resolves this itself (`bat --config-file`), and BAT_CONFIG_PATH overrides it.
# `detect.py` asks the binary rather than reproducing the search order.
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "bat" / "config"

BASHRC_BLOCK = "devstuff: bat"


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

DARK = "dark"
LIGHT = "light"
TERMINAL = "terminal"


@dataclass(frozen=True)
class Theme:
    name: str
    mode: str  # dark | light | terminal
    note: str = ""


# Shipped with bat 0.26.1. Used when the binary cannot be asked; the live list wins.
THEMES: dict[str, Theme] = {
    theme.name: theme
    for theme in (
        Theme("1337", DARK),
        Theme("Catppuccin Frappe", DARK),
        Theme("Catppuccin Latte", LIGHT),
        Theme("Catppuccin Macchiato", DARK),
        Theme("Catppuccin Mocha", DARK),
        Theme("Coldark-Cold", LIGHT),
        Theme("Coldark-Dark", DARK),
        Theme("DarkNeon", DARK),
        Theme("Dracula", DARK),
        Theme("GitHub", LIGHT),
        Theme("Monokai Extended", DARK),
        Theme("Monokai Extended Bright", DARK),
        Theme("Monokai Extended Light", LIGHT),
        Theme("Monokai Extended Origin", DARK),
        Theme("Nord", DARK),
        Theme("OneHalfDark", DARK),
        Theme("OneHalfLight", LIGHT),
        # Hand-corrected: the luminance measurement puts both Solarized variants at
        # ~130 because they share a palette. The names are the reliable signal here.
        Theme("Solarized (dark)", DARK),
        Theme("Solarized (light)", LIGHT),
        Theme("Sublime Snazzy", DARK),
        Theme("TwoDark", DARK),
        Theme("Visual Studio Dark+", DARK),
        Theme("ansi", TERMINAL, "Uses your terminal's own 16 colours."),
        Theme("base16", TERMINAL, "Uses your terminal's own 16 colours."),
        Theme("base16-256", TERMINAL, "Uses your terminal's 256-colour palette."),
        Theme("gruvbox-dark", DARK),
        Theme("gruvbox-light", LIGHT),
        Theme("zenburn", DARK),
    )
}

DEFAULT_DARK_THEME = "Monokai Extended"
DEFAULT_LIGHT_THEME = "GitHub"

# bat's own default: pick a light or dark theme from the terminal's colours.
AUTO_THEME = "auto"


# ---------------------------------------------------------------------------
# Style components
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    key: str
    description: str


# Read from `bat --help`, not recalled. `default`, `full`, `auto` and `plain` are
# *aggregates* rather than components and are deliberately absent: the wizard always
# writes an explicit list, so that what the config says is what you get.
COMPONENTS: dict[str, Component] = {
    component.key: component
    for component in (
        Component("numbers", "Line numbers in the side bar"),
        Component("grid", "Vertical and horizontal rules around the content"),
        Component("header-filename", "The filename above the content"),
        Component("header-filesize", "The file size above the content"),
        Component("changes", "Git modification markers in the side bar"),
        Component("rule", "A horizontal line between files"),
        Component("snip", "A marker where line ranges are skipped"),
    )
}

# bat's own defaults, from `bat --help`: "changes, grid, header-filename, numbers,
# snip". Emitting exactly this set means the key can be omitted.
DEFAULT_COMPONENTS = ("changes", "grid", "header-filename", "numbers", "snip")

# Pairs bat itself complains about. Found by sweeping all 21 component pairs through
# the real binary and reading stderr, not by reasoning about what overlaps: exactly
# one pair warns, and it is the one an "enable everything" preset would obviously
# contain. Warned about for a user who builds one; no shipped preset may hold one,
# which is a test.
COMPONENT_CONFLICTS: tuple[tuple[str, str, str], ...] = (
    ("grid", "rule", "'rule' is a subset of 'grid' and will not be visible."),
)


def component_conflicts(components: list[str] | tuple[str, ...]) -> list[str]:
    chosen = set(components)
    return [why for a, b, why in COMPONENT_CONFLICTS if a in chosen and b in chosen]


# ---------------------------------------------------------------------------
# Other settings
# ---------------------------------------------------------------------------

PAGING = ("auto", "never", "always")
WRAP = ("auto", "never", "character")
ITALIC = ("never", "always")


@dataclass(frozen=True)
class Setting:
    key: str
    flag: str
    label: str
    description: str
    default: object = None


SETTINGS: dict[str, Setting] = {
    "theme": Setting("theme", "--theme", "Theme", "Colours for syntax highlighting", AUTO_THEME),
    "theme_dark": Setting(
        "theme_dark", "--theme-dark", "Dark theme", "Used when the terminal is dark", ""
    ),
    "theme_light": Setting(
        "theme_light", "--theme-light", "Light theme", "Used when the terminal is light", ""
    ),
    "style": Setting("style", "--style", "Style", "Which decorations to draw", None),
    "paging": Setting(
        "paging", "--paging", "Paging", "Whether output goes through a pager", "auto"
    ),
    "pager": Setting("pager", "--pager", "Pager", "The pager command to use", ""),
    "wrap": Setting("wrap", "--wrap", "Wrapping", "How long lines are wrapped", "auto"),
    "italic_text": Setting(
        "italic_text", "--italic-text", "Italics", "Whether to emit italic escapes", "never"
    ),
    "tabs": Setting("tabs", "--tabs", "Tab width", "Spaces a tab expands to", 4),
}


# ---------------------------------------------------------------------------
# Shell integration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellBit:
    key: str
    label: str
    description: str
    line: str
    caution: str = ""


# These go into ~/.bashrc through `base.patch_bashrc`, which ends its block at the
# first blank line — so none of these lines may be blank or contain one.
SHELL_BITS: dict[str, ShellBit] = {
    bit.key: bit
    for bit in (
        ShellBit(
            key="manpager",
            label="Syntax-highlighted man pages",
            description="Renders `man` through bat",
            line=(
                'export MANPAGER="sh -c \'col -bx | bat --language man --style plain\'"'
            ),
        ),
        ShellBit(
            key="manroffopt",
            label="Fix bold and underline in man pages",
            description="Without this, groff's overstriking confuses the highlighter",
            line='export MANROFFOPT="-c"',
        ),
        ShellBit(
            key="help",
            label="`bathelp` for --help output",
            description="A function that pipes any command's --help through bat",
            line="bathelp() { \"$@\" --help 2>&1 | bat --language help --style plain; }",
        ),
        ShellBit(
            key="cat_alias",
            label="Replace `cat` with bat",
            description="alias cat='bat --paging=never'",
            line="alias cat='bat --paging=never'",
            caution=(
                "Scripts that call `cat` still get the real one (aliases are "
                "interactive-only), but anything you pipe by hand now goes through bat."
            ),
        ),
    )
}


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    values: dict[str, object] = field(default_factory=dict)


PRESETS: dict[str, Preset] = {
    "balanced": Preset(
        key="balanced",
        label="Balanced",
        description="bat's defaults, with a theme that follows your terminal.",
        values={
            "theme": AUTO_THEME,
            "theme_dark": DEFAULT_DARK_THEME,
            "theme_light": DEFAULT_LIGHT_THEME,
            "components": list(DEFAULT_COMPONENTS),
        },
    ),
    "minimal": Preset(
        key="minimal",
        label="Minimal",
        description="No decorations and no pager — closest to plain `cat`.",
        values={"components": [], "paging": "never"},
    ),
    "numbers": Preset(
        key="numbers",
        label="Line numbers only",
        description="Numbers and nothing else. Copy-pasteable output.",
        values={"components": ["numbers"], "paging": "never"},
    ),
    "review": Preset(
        key="review",
        label="Code review",
        description="Numbers, git changes, grid and both headers.",
        values={
            # Deliberately not `list(COMPONENTS)`: 'rule' is a subset of 'grid' and
            # bat warns that it will not be visible. An "enable everything" preset is
            # exactly how that pair gets shipped by accident.
            "components": [
                key for key in COMPONENTS if key not in ("rule",)
            ],
            "paging": "auto",
            "italic_text": "always",
        },
    ),
    "piping": Preset(
        key="piping",
        label="Friendly to pipes",
        description="No pager, no wrapping, decorations off — for use inside other commands.",
        values={"components": [], "paging": "never", "wrap": "never"},
    ),
    "current": Preset(
        key="current",
        label="Whatever is configured now",
        description="Start from the existing config and adjust it.",
        values={},
    ),
    "empty": Preset(
        key="empty",
        label="Start from nothing",
        description="An empty config — every bat default, explicitly.",
        values={},
    ),
}

DEFAULT_PRESET = "balanced"


# ---------------------------------------------------------------------------
# The config
# ---------------------------------------------------------------------------


@dataclass
class BatConfig:
    preset: str = DEFAULT_PRESET

    theme: str = AUTO_THEME
    theme_dark: str = ""
    theme_light: str = ""

    components: list[str] = field(default_factory=lambda: list(DEFAULT_COMPONENTS))

    paging: str = "auto"
    pager: str = ""
    wrap: str = "auto"
    italic_text: str = "never"
    tabs: int = 4

    # Lines from an existing config that this wizard does not model, kept verbatim.
    extra: list[str] = field(default_factory=list)

    shell_bits: list[str] = field(default_factory=list)
    target: Path = DEFAULT_CONFIG_PATH

    # -- derived views ------------------------------------------------------

    def style(self) -> str:
        """The `--style` value. `plain` is how bat spells "no components"."""
        return ",".join(self.components) if self.components else "plain"

    def uses_auto_theme(self) -> bool:
        return self.theme == AUTO_THEME

    def themes_in_use(self) -> list[str]:
        """Every theme name this config names, for validating against the real list."""
        if self.uses_auto_theme():
            return [name for name in (self.theme_dark, self.theme_light) if name]
        return [self.theme] if self.theme else []

    def default_components(self) -> bool:
        return tuple(self.components) == tuple(DEFAULT_COMPONENTS)

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.uses_auto_theme() and not (self.theme_dark or self.theme_light):
            out.append(
                "theme=auto with neither --theme-dark nor --theme-light set is just "
                "bat's default behaviour."
            )
        out.extend(component_conflicts(self.components))
        if "header-filesize" in self.components and "header-filename" not in self.components:
            out.append(
                "header-filesize without header-filename shows a size with no name above it."
            )
        if self.paging == "always" and self.pager and "F" not in self.pager:
            out.append(
                "paging=always with a pager lacking -F means short files still open the "
                "pager and need quitting."
            )
        if "cat_alias" in self.shell_bits:
            out.append(SHELL_BITS["cat_alias"].caution)
        if self.wrap == "never" and self.paging != "never":
            out.append("wrap=never inside a pager truncates long lines rather than scrolling.")
        return out


__all__ = [
    "AUTO_THEME",
    "BASHRC_BLOCK",
    "COMPONENT_CONFLICTS",
    "COMPONENTS",
    "CONFIG_FILE",
    "component_conflicts",
    "DARK",
    "DEFAULT_COMPONENTS",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DARK_THEME",
    "DEFAULT_LIGHT_THEME",
    "DEFAULT_PRESET",
    "ITALIC",
    "LIGHT",
    "PAGING",
    "PRESETS",
    "SETTINGS",
    "SHELL_BITS",
    "TERMINAL",
    "THEMES",
    "WRAP",
    "BatConfig",
    "Component",
    "Preset",
    "Setting",
    "ShellBit",
    "Theme",
]
