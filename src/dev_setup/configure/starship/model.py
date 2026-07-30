"""The complete state of a starship prompt the wizard can build.

Everything the emitter and both preview renderers need lives in these tables, so
adding a section or a palette is a data change. See docs/specs/starship-config/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Style presets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    nerd_font: bool
    powerline: bool
    prompt_symbol: str


PRESETS: dict[str, Preset] = {
    "plain": Preset(
        key="plain",
        label="Plain text",
        description="Words instead of icons — renders in any terminal, any font.",
        nerd_font=False,
        powerline=False,
        prompt_symbol="$",
    ),
    "icons": Preset(
        key="icons",
        label="Icons",
        description="Nerd Font glyphs on a transparent background.",
        nerd_font=True,
        powerline=False,
        prompt_symbol="❯",  # ❯
    ),
    "powerline": Preset(
        key="powerline",
        label="Powerline",
        description="Solid colour bars with arrow separators.",
        nerd_font=True,
        powerline=True,
        prompt_symbol="❯",  # ❯
    ),
}

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

# Every palette defines these nine roles and nothing else, so the emitter has one
# code path for all of them and a new palette is nine values (SD-5). Sections
# reference roles by meaning, never by colour.
ROLES = ("dir", "git", "lang", "infra", "shell", "ok", "err", "muted", "bar_text")


@dataclass(frozen=True)
class Palette:
    key: str
    label: str
    description: str
    colors: dict[str, str]


PALETTES: dict[str, Palette] = {
    "terminal": Palette(
        key="terminal",
        label="Terminal colours",
        description="ANSI names — inherits whatever theme your terminal already uses.",
        colors={
            "dir": "blue",
            "git": "green",
            "lang": "yellow",
            "infra": "purple",
            "shell": "cyan",
            "ok": "green",
            "err": "red",
            "muted": "bright-black",
            "bar_text": "black",
        },
    ),
    "catppuccin_mocha": Palette(
        key="catppuccin_mocha",
        label="Catppuccin Mocha",
        description="Soft pastels on a dark background.",
        colors={
            "dir": "#89b4fa",
            "git": "#a6e3a1",
            "lang": "#f9e2af",
            "infra": "#cba6f7",
            "shell": "#94e2d5",
            "ok": "#a6e3a1",
            "err": "#f38ba8",
            "muted": "#6c7086",
            "bar_text": "#1e1e2e",
        },
    ),
    "nord": Palette(
        key="nord",
        label="Nord",
        description="Cool, low-contrast arctic blues.",
        colors={
            "dir": "#81a1c1",
            "git": "#a3be8c",
            "lang": "#ebcb8b",
            "infra": "#b48ead",
            "shell": "#88c0d0",
            "ok": "#a3be8c",
            "err": "#bf616a",
            "muted": "#4c566a",
            "bar_text": "#2e3440",
        },
    ),
    "gruvbox_dark": Palette(
        key="gruvbox_dark",
        label="Gruvbox Dark",
        description="Warm retro earth tones.",
        colors={
            "dir": "#83a598",
            "git": "#b8bb26",
            "lang": "#fabd2f",
            "infra": "#d3869b",
            "shell": "#8ec07c",
            "ok": "#b8bb26",
            "err": "#fb4934",
            "muted": "#665c54",
            "bar_text": "#282828",
        },
    ),
    "tokyo_night": Palette(
        key="tokyo_night",
        label="Tokyo Night",
        description="Saturated neon on near-black.",
        colors={
            "dir": "#7aa2f7",
            "git": "#9ece6a",
            "lang": "#e0af68",
            "infra": "#bb9af7",
            "shell": "#7dcfff",
            "ok": "#9ece6a",
            "err": "#f7768e",
            "muted": "#565f89",
            "bar_text": "#1a1b26",
        },
    ),
}

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

GROUPS = ("Context", "Location", "Git", "Languages", "Infrastructure", "Shell")


@dataclass(frozen=True)
class Section:
    """One starship module, as the wizard understands it.

    `key` is the real starship module name — it is what goes in `format` and what
    names the `[table]`. `body` is the module's own format body; `$symbol` in it is
    what makes `symbol` get emitted (including as an empty string for `plain`, so
    starship's built-in glyph cannot leak through).
    """

    key: str
    label: str
    group: str
    role: str
    body: str
    sample: str
    icon: str = ""
    plain: str = ""
    default: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    # Which key carries the colour. Almost every module spells it `style`, but
    # `username` wants `style_user` (it picks between that and `style_root` itself).
    style_key: str = "style"

    @property
    def takes_symbol(self) -> bool:
        return "$symbol" in self.body


# Declaration order is the canonical prompt order — the wizard's checkbox may hand
# selections back in any order, but the prompt is always built from this sequence.
# Icons are written as escapes with their Nerd Font name so they stay reviewable.
SECTIONS: tuple[Section, ...] = (
    Section(
        key="username",
        label="Username",
        group="Context",
        role="shell",
        body="$user",
        sample="dev",
        # Only interesting when it is not you on your own box. `style_root` has to be
        # set too or a root shell falls back to starship's off-palette bold red — the
        # role name resolves against our palette, so a static string is enough.
        extra={"show_always": False, "style_root": "bold fg:err"},
        style_key="style_user",
    ),
    Section(
        key="hostname",
        label="Hostname (over SSH)",
        group="Context",
        role="shell",
        body="$ssh_symbol$hostname",
        sample="devbox",
        extra={"ssh_only": True},
    ),
    Section(
        key="directory",
        label="Current directory",
        group="Location",
        role="dir",
        body="$path$read_only",
        sample="api",
        # No icon: `directory` is the one selectable module with no `symbol` key at
        # all (a folder glyph has to be written into `format` by hand). The invariant
        # that icon/plain only exist where the body takes `$symbol` is under test.
        default=True,
        extra={"truncation_length": 3, "truncate_to_repo": True},
    ),
    Section(
        key="git_branch",
        label="Git branch",
        group="Git",
        role="git",
        body="$symbol$branch",
        sample="main",
        icon="\ue0a0 ",  # nf-pl-branch
        plain="on ",
        default=True,
    ),
    Section(
        key="git_status",
        label="Git status (dirty/ahead/behind)",
        group="Git",
        role="err",
        # Literal brackets around the status flags; emitted inside a TOML literal
        # string so the backslashes reach starship untouched.
        body="\\[$all_status$ahead_behind\\]",
        sample="[+2 ?1]",
        default=True,
    ),
    Section(
        key="git_state",
        label="Git operation in progress (rebase, merge)",
        group="Git",
        role="err",
        body="$state( $progress_current/$progress_total)",
        sample="REBASING 1/3",
    ),
    Section(
        key="nodejs",
        label="Node.js",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v22.11.0",
        icon="\ue718 ",  # nf-dev-nodejs_small
        plain="node ",
        default=True,
    ),
    Section(
        key="python",
        label="Python",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v3.12.3",
        icon="\ue73c ",  # nf-dev-python
        plain="py ",
        default=True,
    ),
    Section(
        key="rust",
        label="Rust",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v1.79.0",
        icon="\ue7a8 ",  # nf-dev-rust
        plain="rust ",
    ),
    Section(
        key="golang",
        label="Go",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v1.22.5",
        icon="\ue627 ",  # nf-seti-go
        plain="go ",
    ),
    Section(
        key="java",
        label="Java",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v21.0.3",
        icon="\ue256 ",  # nf-dev-java
        plain="java ",
    ),
    Section(
        key="php",
        label="PHP",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v8.3.7",
        icon="\ue73d ",  # nf-dev-php
        plain="php ",
    ),
    Section(
        key="ruby",
        label="Ruby",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v3.3.1",
        icon="\ue791 ",  # nf-dev-ruby
        plain="ruby ",
    ),
    Section(
        key="package",
        label="Package version (package.json, pyproject, …)",
        group="Languages",
        role="lang",
        body="$symbol$version",
        sample="v1.4.0",
        icon="\U000f03d7 ",  # nf-md-package_variant_closed
        plain="pkg ",
    ),
    Section(
        key="docker_context",
        label="Docker context",
        group="Infrastructure",
        role="infra",
        body="$symbol$context",
        sample="desktop",
        icon="\U000f0868 ",  # nf-md-docker
        plain="docker ",
    ),
    Section(
        key="kubernetes",
        label="Kubernetes context",
        group="Infrastructure",
        role="infra",
        body="$symbol$context",
        sample="prod-eu",
        icon="\U000f10fe ",  # nf-md-kubernetes
        plain="k8s ",
        # starship ships this module disabled; selecting it has to turn it back on.
        extra={"disabled": False},
    ),
    Section(
        key="aws",
        label="AWS profile",
        group="Infrastructure",
        role="infra",
        body="$symbol$profile",
        sample="prod",
        icon="\ue7ad ",  # nf-dev-aws
        plain="aws ",
    ),
    Section(
        key="terraform",
        label="Terraform workspace",
        group="Infrastructure",
        role="infra",
        body="$symbol$workspace",
        sample="staging",
        icon="\U000f1062 ",  # nf-md-terraform
        plain="tf ",
    ),
    Section(
        key="nix_shell",
        label="Nix shell",
        group="Infrastructure",
        role="infra",
        body="$symbol$state",
        sample="impure",
        icon="\U000f1105 ",  # nf-md-nix
        plain="nix ",
    ),
    Section(
        key="cmd_duration",
        label="Last command duration",
        group="Shell",
        role="shell",
        body="took $duration",
        sample="took 2s",
        default=True,
        extra={"min_time": 2000},
    ),
    Section(
        key="jobs",
        label="Background jobs",
        group="Shell",
        role="shell",
        body="$symbol$number",
        sample="2",
        icon="✦",  # ✦ — not font-dependent
        plain="jobs ",
        extra={"number_threshold": 1},
    ),
    # `battery` is deliberately absent: it is the one module that takes neither a
    # `style` nor a `symbol` key (both live in its `[[battery.display]]` threshold
    # array), so offering it would mean three quirk fields for one section. Users who
    # want it can add it by hand — the generated file links the module reference.
    Section(
        key="time",
        label="Clock",
        group="Shell",
        role="muted",
        body="$time",
        sample="14:32",
        # Disabled by default in starship, same as kubernetes.
        extra={"disabled": False, "time_format": "%R"},
    ),
)

SECTIONS_BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}

# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    key: str
    label: str
    description: str
    two_line: bool
    right_prompt: bool


LAYOUTS: dict[str, Layout] = {
    "single": Layout(
        key="single",
        label="Single line",
        description="Everything and the prompt symbol on one line.",
        two_line=False,
        right_prompt=False,
    ),
    "two_line": Layout(
        key="two_line",
        label="Two lines",
        description="Prompt symbol on its own line — long paths never crowd what you type.",
        two_line=True,
        right_prompt=False,
    ),
    "two_line_right": Layout(
        key="two_line_right",
        label="Two lines, shell info right-aligned",
        # starship's right_format needs a shell with a right-prompt mechanism; bash
        # only gets one via ble.sh, and devstuff installs the plain bash hook.
        description="Duration/clock at the right edge — needs zsh, fish or nushell.",
        two_line=True,
        right_prompt=True,
    ),
}

DEFAULT_PRESET = "icons"
DEFAULT_PALETTE = "catppuccin_mocha"
DEFAULT_LAYOUT = "two_line"


def default_sections() -> list[str]:
    return [s.key for s in SECTIONS if s.default]


@dataclass
class StarshipConfig:
    preset: str = DEFAULT_PRESET
    palette: str = DEFAULT_PALETTE
    layout: str = DEFAULT_LAYOUT
    sections: list[str] = field(default_factory=default_sections)
    blank_line: bool = True

    # -- resolved views the emitter and previews share ----------------------

    @property
    def preset_spec(self) -> Preset:
        return PRESETS[self.preset]

    @property
    def palette_spec(self) -> Palette:
        return PALETTES[self.palette]

    @property
    def layout_spec(self) -> Layout:
        return LAYOUTS[self.layout]

    def selected(self) -> list[Section]:
        """Selected sections in canonical order, ignoring unknown keys."""
        chosen = set(self.sections)
        return [s for s in SECTIONS if s.key in chosen]

    def split(self) -> tuple[list[Section], list[Section]]:
        """(left, right) — the right prompt takes the Shell group, when enabled."""
        selected = self.selected()
        if not self.layout_spec.right_prompt:
            return selected, []
        left = [s for s in selected if s.group != "Shell"]
        right = [s for s in selected if s.group == "Shell"]
        return left, right

    def symbol(self, section: Section) -> str:
        """The glyph for this section under the current preset."""
        return section.icon if self.preset_spec.nerd_font else section.plain

    def color(self, role: str) -> str:
        return self.palette_spec.colors[role]
