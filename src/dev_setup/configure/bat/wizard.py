"""The interactive `devstuff configure bat` flow.

The preview is the point. bat renders a sample file with the candidate config on
every pass through the review loop, so choosing a theme is looking at it rather than
reading its name — the same arrangement as the starship wizard, for the same reason.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import questionary

from dev_setup import base, ui
from dev_setup.configure.bat import detect, preview, render
from dev_setup.configure.bat.model import (
    AUTO_THEME,
    BASHRC_BLOCK,
    COMPONENTS,
    DARK,
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    ITALIC,
    LIGHT,
    PAGING,
    PRESETS,
    SHELL_BITS,
    THEMES,
    WRAP,
    BatConfig,
)


def config_path() -> Path:
    """Where bat says its config lives — asked of the binary, not reproduced."""
    return detect.inspect().path


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _select(prompt: str, choices: list, current: object) -> str:
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or str(current)


def _ask_preset(cfg: BatConfig, found: detect.Bat) -> None:
    choices = [
        questionary.Choice(title=preset.label, value=preset.key, description=preset.description)
        for preset in PRESETS.values()
        if not (preset.key == "current" and not found.has())
    ]
    detect.apply_preset(cfg, _select("Start from:", choices, cfg.preset), found)


def _theme_choices(found: detect.Bat, *, mode: str | None = None) -> list:
    """Every theme this bat can load, annotated with what it is for.

    The live list is authoritative — a user can build their own themes — so a theme
    the shipped table has never heard of is offered without a label rather than
    hidden.
    """
    choices = []
    for name in detect.known_themes(found):
        known = THEMES.get(name)
        if mode and known and known.mode != mode:
            continue
        suffix = ""
        if known is None:
            suffix = "  (yours)"
        elif known.note:
            suffix = f"  {known.note}"
        elif not mode:
            suffix = f"  ({known.mode})"
        choices.append(questionary.Choice(title=f"{name}{suffix}", value=name))
    return choices


def _ask_theme(cfg: BatConfig, found: detect.Bat) -> None:
    mode = _select(
        "How should the theme be chosen?",
        [
            questionary.Choice(
                title="Follow the terminal — a dark theme and a light one",
                value=AUTO_THEME,
                description="bat's default. It picks by the terminal's colours at run time.",
            ),
            questionary.Choice(
                title="One theme, always",
                value="fixed",
                description="The same colours whatever the terminal is doing.",
            ),
        ],
        AUTO_THEME if cfg.uses_auto_theme() else "fixed",
    )

    if mode == AUTO_THEME:
        cfg.theme = AUTO_THEME
        cfg.theme_dark = _select(
            "Theme for a dark terminal:",
            _theme_choices(found, mode=DARK),
            cfg.theme_dark or DEFAULT_DARK_THEME,
        )
        cfg.theme_light = _select(
            "Theme for a light terminal:",
            _theme_choices(found, mode=LIGHT),
            cfg.theme_light or DEFAULT_LIGHT_THEME,
        )
        return

    cfg.theme_dark = ""
    cfg.theme_light = ""
    current = cfg.theme if cfg.theme != AUTO_THEME else DEFAULT_DARK_THEME
    cfg.theme = _select("Theme:", _theme_choices(found), current)


def _ask_components(cfg: BatConfig) -> None:
    chosen = set(cfg.components)
    width = max(len(key) for key in COMPONENTS) + 2
    choices = [
        questionary.Choice(
            title=[
                ("class:text", f"{component.key:<{width}}"),
                ("class:instruction", component.description),
            ],
            value=component.key,
            checked=component.key in chosen,
        )
        for component in COMPONENTS.values()
    ]
    cfg.components = list(ui.checkbox(
        "Decorations to draw:", choices=choices, instruction="(Space toggle · Enter confirm)"
    ))


def _ask_behaviour(cfg: BatConfig) -> None:
    cfg.paging = _select(
        "Send output through a pager?",
        [
            questionary.Choice(
                title="auto", value="auto", description="Only when it will not fit the screen"
            ),
            questionary.Choice(
                title="never", value="never", description="Never — behaves like cat"
            ),
            questionary.Choice(title="always", value="always", description="Always"),
        ],
        cfg.paging if cfg.paging in PAGING else "auto",
    )
    if cfg.paging != "never":
        ui.dim("  Blank uses bat's own default (less with the right flags).")
        cfg.pager = ui.text_input("Pager command:", default=cfg.pager)
    else:
        cfg.pager = ""

    cfg.wrap = _select(
        "Wrap long lines?",
        [
            questionary.Choice(title="auto", value="auto", description="Wrap at the terminal width"),
            questionary.Choice(title="never", value="never", description="Let them run off"),
            questionary.Choice(
                title="character", value="character", description="Wrap mid-word if need be"
            ),
        ],
        cfg.wrap if cfg.wrap in WRAP else "auto",
    )
    cfg.italic_text = _select(
        "Use italics for comments?",
        [
            questionary.Choice(
                title="never",
                value="never",
                description="bat's default — many terminals render italics as inverse video",
            ),
            questionary.Choice(title="always", value="always", description="Emit italic escapes"),
        ],
        cfg.italic_text if cfg.italic_text in ITALIC else "never",
    )
    while True:
        answer = ui.text_input("Spaces per tab:", default=str(cfg.tabs))
        if answer.isdigit():
            cfg.tabs = int(answer)
            break
        ui.error("A whole number.")


def _ask_shell(cfg: BatConfig) -> None:
    """The half of bat's value that is not in its config file."""
    chosen = set(cfg.shell_bits)
    choices = [
        questionary.Choice(
            title=bit.label,
            value=bit.key,
            checked=bit.key in chosen,
            description=bit.description,
        )
        for bit in SHELL_BITS.values()
    ]
    cfg.shell_bits = list(ui.checkbox(
        "Add to ~/.bashrc:", choices=choices, instruction="(Space toggle · Enter confirm)"
    ))
    if "cat_alias" in cfg.shell_bits:
        ui.warn(SHELL_BITS["cat_alias"].caution)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _show_preview(cfg: BatConfig, found: detect.Bat) -> None:
    ui.console.print()
    for label, value in render.summary(cfg):
        ui.console.print(f"  [bold cyan]{label:<16}[/] {value}")
    ui.console.print()

    rendered = preview.render_live(cfg, width=min(ui.console.width - 6, 96))
    if rendered is not None and rendered[0].strip():
        stdout, stderr = rendered
        ui.dim(f"  bat {found.version or detect.version()} rendering {preview.SAMPLE_NAME}:")
        ui.console.print()
        # Written straight through: this is bat's own output, escapes and all, and
        # anything that re-styles it would stop it being a preview.
        ui.console.file.write("".join(f"  {line}\n" for line in stdout.splitlines()))
        ui.console.file.flush()
        warning = next((ln for ln in stderr.splitlines() if "warning" in ln), "")
        if warning:
            ui.console.print()
            ui.warn(warning.strip())
    else:
        ui.dim("  bat is not installed, so this is what the decorations would be:")
        ui.console.print()
        for line in preview.describe(cfg):
            ui.console.print(f"    [dim]{line}[/]")

    if cfg.shell_bits:
        ui.console.print()
        ui.dim("  ~/.bashrc additions:")
        ui.code_block(render.shell_block(cfg), language="bash")

    for warning in cfg.warnings():
        ui.warn(warning)
    if found.env_overrides:
        ui.console.print()
        # These beat the config file, so a theme chosen here would have no effect.
        ui.warn(
            "Set in your environment and overriding this config: "
            + ", ".join(f"{k}={v or '(empty)'}" for k, v in found.env_overrides.items())
        )
    ui.console.print()


def _run_check(cfg: BatConfig, found: detect.Bat, *, quiet_when_ok: bool = False) -> bool:
    report = preview.check(cfg, found)
    if report.ok and quiet_when_ok:
        ui.dim(f"  {len(report.checks)} checks pass.")
        return True
    ui.console.print()
    ui.console.print(f"  [dim]checked with[/] [bold]bat {report.version or 'unknown'}[/]")
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        ui.console.print(f"  {mark} {check.name}  [dim]{check.detail}[/]")
    ui.console.print()
    return report.ok


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak.{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, dest)
    return dest


def save(cfg: BatConfig, path: Path) -> tuple[Path, Path | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = backup(path)
    path.write_text(render.to_text(cfg), encoding="utf-8")
    return path, saved


def _apply_shell(cfg: BatConfig, found: detect.Bat) -> None:
    """Patch ~/.bashrc, replacing any block this wizard wrote before.

    `base.patch_bashrc` is a no-op when its marker is already present, so an existing
    block is removed first — otherwise re-running the wizard would silently keep the
    old integration.
    """
    block = render.shell_block(cfg)
    if found.bashrc_patched:
        base.remove_bashrc_block(BASHRC_BLOCK)
    if not block:
        if found.bashrc_patched:
            ui.dim("  Removed the previous ~/.bashrc block.")
        return
    if base.patch_bashrc(BASHRC_BLOCK, block):
        ui.success("Added the shell integration to ~/.bashrc")
        ui.dim("  Open a new shell, or:  source ~/.bashrc")


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save this configuration",
    "preset": "Start from a different preset",
    "theme": "Change the theme",
    "components": "Change which decorations are drawn",
    "behaviour": "Change paging, wrapping, italics and tabs",
    "shell": "Change the ~/.bashrc integration",
    "check": "Check this against bat",
    "text": "Show the generated config file",
    "cancel": "Cancel without saving",
}


def _report(found: detect.Bat) -> None:
    if not found.installed:
        ui.warn("bat is not installed — the wizard can still write a config, but the")
        ui.warn("preview will be a description rather than bat's own output.")
        ui.dim("  Install it with:  devstuff install bat")
    else:
        ui.dim(f"bat {found.version} · {len(found.themes)} themes available")
    if found.has():
        ui.dim(
            f"Existing config: {found.path} "
            f"({len(found.existing_flags)} options"
            + (", written by this wizard" if found.generated else "")
            + ")"
        )
    else:
        ui.dim(f"No config at {found.path} yet.")
    if found.bashrc_patched:
        ui.dim("~/.bashrc already carries this wizard's bat block.")
    if found.env_overrides:
        ui.warn(
            "These environment variables override the config file: "
            + ", ".join(found.env_overrides)
        )


def run(*, target: Path | None = None) -> BatConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled."""
    found = detect.inspect()
    cfg = detect.suggest(found)
    if target is not None:
        cfg.target = target

    ui.section("Configure bat")
    ui.dim("Pick a starting point and watch bat render a sample file with it. Nothing")
    ui.dim("is written until you save. Ctrl-C to bail out.")
    ui.console.print()
    _report(found)

    ui.console.print()
    _ask_preset(cfg, found)
    _ask_theme(cfg, found)

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
        if action == "text":
            ui.code_block(render.to_text(cfg), language="ini")
            continue
        if action == "check":
            _run_check(cfg, found)
            continue
        if action == "preset":
            _ask_preset(cfg, found)
            _ask_theme(cfg, found)
        elif action == "theme":
            _ask_theme(cfg, found)
        elif action == "components":
            _ask_components(cfg)
        elif action == "behaviour":
            _ask_behaviour(cfg)
        elif action == "shell":
            _ask_shell(cfg)

    path = target or cfg.target

    if not _run_check(cfg, found, quiet_when_ok=True) and not ui.confirm(
        "Some checks failed. Save anyway?", default=False
    ):
        ui.dim("Nothing was written.")
        return None

    if path.exists() and not found.generated and target is None:
        ui.console.print()
        ui.warn(f"{path} was not written by this wizard — it will be replaced.")
        lines = render.diff(found.existing_text, render.to_text(cfg))
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

    if target is None:
        _apply_shell(cfg, found)
    else:
        ui.dim(f"  Try it:  BAT_CONFIG_PATH={written} bat <file>")

    ui.console.print()
    ui.dim(f"Re-run any time:  devstuff configure bat   ·   edit: {written}")
    ui.console.print()
    return cfg


__all__ = ["backup", "config_path", "run", "save"]
