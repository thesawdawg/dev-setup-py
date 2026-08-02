"""The data behind the lazygit wizard. Everything else reads these tables.

**How the key set was established, and why it took a trick.**

lazygit's validation is lopsided: a value of the wrong *type* is a hard startup
error, and an unknown *key* is silently ignored. So a config carrying settings from
an old guide starts perfectly and does nothing.

The obvious way to check a key is to look for it in `lazygit --config`, which prints
the defaults. That is wrong, and it was measured wrong: **the dump omits any setting
with no default**, so `git.paging.pager` and the whole `os:` section are absent from
it while being perfectly valid. Trusting the dump would have made this wizard refuse
to write the single most-wanted lazygit setting there is (delta as the pager).

What does work is turning the type-strictness into a probe: set the key to a value of
obviously the wrong type and start lazygit. A real key produces an unmarshal error; an
unknown key is ignored and lazygit starts. Every `path` below was verified that way
against lazygit 0.62.2, and the probe is what `git.paging.useConfig` — present in most
delta guides — failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "config.yml"
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "lazygit"

# lazygit writes `expandAll: =` in its own default keybindings, and PyYAML's
# SafeLoader maps a bare `=` to the special `tag:yaml.org,2002:value` and refuses to
# load the document. Any code here that parses a lazygit config has to allow for it —
# see `render.load`.
YAML_VALUE_TAG = "tag:yaml.org,2002:value"


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    description: str


GROUPS: dict[str, Group] = {
    "appearance": Group("appearance", "Appearance", "Icons, panels and what is on screen"),
    "diff": Group("diff", "Diffs", "How diffs are rendered"),
    "git": Group("git", "Git behaviour", "What lazygit does to your repository"),
    "editor": Group("editor", "Editor", "What opens when you press e"),
    "safety": Group("safety", "Prompts", "Which confirmations you get"),
}


@dataclass(frozen=True)
class Setting:
    key: str  # field name on LazygitConfig
    path: str  # dotted path into config.yml — verified real by the type probe
    label: str
    description: str
    group: str
    kind: str  # bool | int | float | str | list
    default: object = None
    choices: tuple[str, ...] = ()
    why: str = ""


SETTINGS: dict[str, Setting] = {
    "nerd_fonts_version": Setting(
        key="nerd_fonts_version",
        path="gui.nerdFontsVersion",
        label="Nerd Font icons",
        description="Which Nerd Font glyph set to draw",
        group="appearance",
        kind="str",
        default="",
        choices=("", "2", "3"),
        why="Empty means no icons. Setting it without the font installed gives boxes "
        "and question marks — which is why the wizard checks for the font first.",
    ),
    "show_icons": Setting(
        key="show_icons",
        path="gui.showIcons",
        label="File type icons",
        description="Draw an icon beside each filename",
        group="appearance",
        kind="bool",
        default=False,
        why="Needs a Nerd Font, same as the setting above.",
    ),
    "show_file_tree": Setting(
        key="show_file_tree",
        path="gui.showFileTree",
        label="File tree",
        description="Show changed files as a tree rather than a flat list",
        group="appearance",
        kind="bool",
        default=True,
    ),
    "show_command_log": Setting(
        key="show_command_log",
        path="gui.showCommandLog",
        label="Command log",
        description="The panel showing the git commands lazygit runs",
        group="appearance",
        kind="bool",
        default=True,
        why="Worth keeping while you are learning what lazygit does on your behalf.",
    ),
    "show_bottom_line": Setting(
        key="show_bottom_line",
        path="gui.showBottomLine",
        label="Bottom line",
        description="The information line at the bottom of the screen",
        group="appearance",
        kind="bool",
        default=True,
    ),
    "show_random_tip": Setting(
        key="show_random_tip",
        path="gui.showRandomTip",
        label="Random tips",
        description="Show a tip in the bottom line",
        group="appearance",
        kind="bool",
        default=True,
    ),
    "mouse_events": Setting(
        key="mouse_events",
        path="gui.mouseEvents",
        label="Mouse support",
        description="Respond to clicks and scrolling",
        group="appearance",
        kind="bool",
        default=True,
        why="Turning it off gives the terminal's own selection back for copy-paste.",
    ),
    "scroll_height": Setting(
        key="scroll_height",
        path="gui.scrollHeight",
        label="Scroll step",
        description="Lines moved per scroll",
        group="appearance",
        kind="int",
        default=2,
    ),
    "side_panel_width": Setting(
        key="side_panel_width",
        path="gui.sidePanelWidth",
        label="Side panel width",
        description="Fraction of the screen the left panels take",
        group="appearance",
        # A float, not a string. The wizard modelled it as a string at first and the
        # default-drift check against `lazygit --config` is what caught it.
        kind="float",
        default=0.3333,
    ),
    "time_format": Setting(
        key="time_format",
        path="gui.timeFormat",
        label="Date format",
        description="Go time layout for commit dates",
        group="appearance",
        kind="str",
        default="02 Jan 06",
    ),
    "pager": Setting(
        key="pager",
        path="git.paging.pager",
        label="Diff pager",
        description="External pager for diffs, such as delta",
        group="diff",
        kind="str",
        default="",
        why="This key has no default, so it does not appear in `lazygit --config` — "
        "which does not make it invalid. Note `useConfig` from older guides is gone.",
    ),
    "color_arg": Setting(
        key="color_arg",
        path="git.paging.colorArg",
        label="Colour argument",
        description="What lazygit passes to git for colour",
        group="diff",
        kind="str",
        default="always",
        choices=("always", "never"),
    ),
    "diff_context_size": Setting(
        key="diff_context_size",
        path="git.diffContextSize",
        label="Diff context lines",
        description="Lines of context around each change",
        group="diff",
        kind="int",
        default=3,
    ),
    "ignore_whitespace": Setting(
        key="ignore_whitespace",
        path="git.ignoreWhitespaceInDiffView",
        label="Ignore whitespace in diffs",
        description="Hide whitespace-only changes",
        group="diff",
        kind="bool",
        default=False,
    ),
    "auto_fetch": Setting(
        key="auto_fetch",
        path="git.autoFetch",
        label="Fetch automatically",
        description="Fetch in the background while lazygit is open",
        group="git",
        kind="bool",
        default=True,
        why="Turn it off on a repository whose remote needs a password or a token.",
    ),
    "fetch_all": Setting(
        key="fetch_all",
        path="git.fetchAll",
        label="Fetch all remotes",
        description="Fetch every remote rather than just origin",
        group="git",
        kind="bool",
        default=True,
    ),
    "sign_off": Setting(
        key="sign_off",
        path="git.commit.signOff",
        label="Sign off commits",
        description="Add a Signed-off-by trailer",
        group="git",
        kind="bool",
        default=False,
    ),
    "disable_force_pushing": Setting(
        key="disable_force_pushing",
        path="git.disableForcePushing",
        label="Disable force pushing",
        description="Remove force push from the interface entirely",
        group="git",
        kind="bool",
        default=False,
        why="lazygit force-pushes with --force-with-lease, but on a shared branch the "
        "safest option is not having the key bound at all.",
    ),
    "log_order": Setting(
        key="log_order",
        path="git.log.order",
        label="Commit ordering",
        description="How the commit list is ordered",
        group="git",
        kind="str",
        default="topo-order",
        choices=("topo-order", "date-order", "author-date-order", "default"),
    ),
    "log_show_graph": Setting(
        key="log_show_graph",
        path="git.log.showGraph",
        label="Commit graph",
        description="Draw the branch graph beside commits",
        group="git",
        kind="str",
        default="always",
        choices=("always", "never", "when-maximised"),
    ),
    "edit_preset": Setting(
        key="edit_preset",
        path="os.editPreset",
        label="Editor",
        description="Which editor opens on `e`",
        group="editor",
        kind="str",
        default="",
        choices=(
            "",
            "vim",
            "nvim",
            "lvim",
            "helix",
            "emacs",
            "nano",
            "micro",
            "kakoune",
            "vscode",
            "sublime",
            "zed",
            "acme",
        ),
        why="A preset knows how to open a file at a line number. `os.edit` is the "
        "escape hatch for an editor with no preset.",
    ),
    "confirm_on_quit": Setting(
        key="confirm_on_quit",
        path="confirmOnQuit",
        label="Confirm on quit",
        description="Ask before quitting",
        group="safety",
        kind="bool",
        default=False,
    ),
    "quit_on_top_level_return": Setting(
        key="quit_on_top_level_return",
        path="quitOnTopLevelReturn",
        label="Escape quits",
        description="Pressing escape at the top level quits",
        group="safety",
        kind="bool",
        default=False,
    ),
    "disable_startup_popups": Setting(
        key="disable_startup_popups",
        path="disableStartupPopups",
        label="Skip startup popups",
        description="Do not show the intro and update popups",
        group="safety",
        kind="bool",
        default=False,
    ),
    "not_a_repository": Setting(
        key="not_a_repository",
        path="notARepository",
        label="Outside a repository",
        description="What to do when started somewhere that is not a git repo",
        group="safety",
        kind="str",
        default="prompt",
        choices=("prompt", "create", "skip", "quit"),
    ),
    "refresh_interval": Setting(
        key="refresh_interval",
        path="refresher.refreshInterval",
        label="Refresh interval (seconds)",
        description="How often lazygit re-reads the repository",
        group="git",
        kind="int",
        default=10,
    ),
    "fetch_interval": Setting(
        key="fetch_interval",
        path="refresher.fetchInterval",
        label="Fetch interval (seconds)",
        description="How often lazygit fetches in the background",
        group="git",
        kind="int",
        default=60,
    ),
}

# Keys that appear in guides and are no longer real. Verified with the type probe:
# each of these is ignored rather than rejected, which is why a config carrying one
# looks fine and does nothing.
RETIRED_KEYS: dict[str, str] = {
    "git.paging.useConfig": (
        "Removed. lazygit reads the pager from `git.paging.pager` directly; there is "
        "no longer a mode that defers to your git config."
    ),
    "gui.theme.lightTheme": (
        "Removed. Set the individual `gui.theme.*` colours instead."
    ),
    "gui.skipUnstageLineWarning": "Renamed to `gui.skipDiscardChangeWarning`.",
}

# Pagers worth offering, with the arguments that actually work as lazygit's pager.
# `--paging=never` matters: lazygit is already the pager, and delta opening its own
# would leave a dead pane.
PAGERS: dict[str, str] = {
    "": "git's own diff output",
    "delta --dark --paging=never": "delta, dark background",
    "delta --light --paging=never": "delta, light background",
    "diff-so-fancy": "diff-so-fancy",
    "ydiff -p cat -s --wrap --width={{columnWidth}}": "ydiff, side by side",
}

NERD_FONT_SETTINGS = ("nerd_fonts_version", "show_icons")


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    values: dict[str, object] = field(default_factory=dict)


PRESETS: dict[str, Preset] = {
    "recommended": Preset(
        key="recommended",
        label="Recommended",
        description="Icons, a readable graph, and the startup popups turned off.",
        values={
            "nerd_fonts_version": "3",
            "show_icons": True,
            "disable_startup_popups": True,
            "log_show_graph": "always",
        },
    ),
    "plain": Preset(
        key="plain",
        label="No icons",
        description="Everything except the Nerd Font glyphs — for a terminal without one.",
        values={
            "nerd_fonts_version": "",
            "show_icons": False,
            "disable_startup_popups": True,
        },
    ),
    "delta": Preset(
        key="delta",
        label="With delta",
        description="Recommended, plus delta as the diff pager.",
        values={
            "nerd_fonts_version": "3",
            "show_icons": True,
            "disable_startup_popups": True,
            "pager": "delta --dark --paging=never",
            "color_arg": "always",
        },
    ),
    "minimal": Preset(
        key="minimal",
        label="Minimal interface",
        description="No command log, no bottom line, no tips — just the panels.",
        values={
            "show_command_log": False,
            "show_bottom_line": False,
            "show_random_tip": False,
            "disable_startup_popups": True,
        },
    ),
    "careful": Preset(
        key="careful",
        label="Careful",
        description="Confirm on quit, no force pushing, no background fetching.",
        values={
            "confirm_on_quit": True,
            "disable_force_pushing": True,
            "auto_fetch": False,
            "disable_startup_popups": True,
        },
    ),
    "current": Preset(
        key="current",
        label="Whatever is configured now",
        description="Start from the existing config.yml and adjust it.",
        values={},
    ),
    "empty": Preset(
        key="empty",
        label="Start from nothing",
        description="An empty config — every lazygit default, explicitly.",
        values={},
    ),
}

DEFAULT_PRESET = "recommended"


@dataclass
class LazygitConfig:
    preset: str = DEFAULT_PRESET

    nerd_fonts_version: str = ""
    show_icons: bool = False
    show_file_tree: bool = True
    show_command_log: bool = True
    show_bottom_line: bool = True
    show_random_tip: bool = True
    mouse_events: bool = True
    scroll_height: int = 2
    side_panel_width: float = 0.3333
    time_format: str = "02 Jan 06"

    pager: str = ""
    color_arg: str = "always"
    diff_context_size: int = 3
    ignore_whitespace: bool = False

    auto_fetch: bool = True
    fetch_all: bool = True
    sign_off: bool = False
    disable_force_pushing: bool = False
    log_order: str = "topo-order"
    log_show_graph: str = "always"
    refresh_interval: int = 10
    fetch_interval: int = 60

    edit_preset: str = ""

    confirm_on_quit: bool = False
    quit_on_top_level_return: bool = False
    disable_startup_popups: bool = False
    not_a_repository: str = "prompt"

    # Whole subtrees read from an existing config that this wizard does not model,
    # merged back into the output untouched.
    extra: dict = field(default_factory=dict)

    target: Path = field(default_factory=lambda: DEFAULT_CONFIG_DIR / CONFIG_FILE)

    # -- derived views ------------------------------------------------------

    def changed(self) -> dict[str, Setting]:
        out: dict[str, Setting] = {}
        for key, setting in SETTINGS.items():
            if getattr(self, key) != setting.default:
                out[key] = setting
        return out

    def wants_icons(self) -> bool:
        return bool(self.nerd_fonts_version) or self.show_icons

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.show_icons and not self.nerd_fonts_version:
            out.append(
                "showIcons is on but nerdFontsVersion is empty, so lazygit falls back "
                "to its plain glyphs."
            )
        if self.pager and self.color_arg == "never":
            out.append(
                "A pager with colorArg=never gets a diff with no colour to work with."
            )
        if self.pager and "--paging=never" not in self.pager and "delta" in self.pager:
            out.append(
                "delta without --paging=never opens its own pager inside lazygit's."
            )
        if self.auto_fetch is False and self.fetch_interval != 60:
            out.append("Auto-fetching is off, so the fetch interval has no effect.")
        if self.refresh_interval < 1:
            out.append("A refresh interval below 1 second will keep the repository busy.")
        return out


__all__ = [
    "CONFIG_FILE",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_PRESET",
    "GROUPS",
    "NERD_FONT_SETTINGS",
    "PAGERS",
    "PRESETS",
    "RETIRED_KEYS",
    "SETTINGS",
    "YAML_VALUE_TAG",
    "Group",
    "LazygitConfig",
    "Preset",
    "Setting",
]
