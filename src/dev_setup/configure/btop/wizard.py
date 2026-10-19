"""The interactive `devstuff configure btop` flow.

Same shape as the bat wizard: a report, a preset, then a review loop of summary +
menu until save. What is different is what the checks *mean*: btop resets some
bad values on load (and rewrites the whole file when it does), keeps others
silently, and rewrites the file itself on any in-app change — so the save path is
`verify` (offline) then `launch` (the real binary, in a hidden terminal), and the
overwrite of an existing file always shows a diff because no generated-by marker
survives btop's rewrites.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import questionary
from rich.markup import escape

from dev_setup import ui
from dev_setup.configure.btop import capability, detect, render, validate
from dev_setup.configure.btop.model import (
    BOXES,
    GPU_BOXES,
    PRESETS,
    SETTINGS,
    BtopConfig,
)


def config_path() -> Path:
    return detect.config_file()


def _known(found: detect.Btop, key: str) -> bool:
    """Offer a setting only when this btop can have it — `keys` None means it
    could not be asked, in which case everything is offered."""
    return found.keys is None or key in found.keys


def _select(prompt: str, choices: list, current: object) -> str:
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or str(current)


# ---------------------------------------------------------------------------
# Report and summary
# ---------------------------------------------------------------------------


def _report(found: detect.Btop) -> None:
    if not found.installed:
        ui.warn("btop is not installed — checks against btop will be skipped.")
        ui.dim("  Install it with:  devstuff install btop")
    else:
        keys = (
            f"{len(found.keys)} settings known"
            if found.keys is not None
            else "key set unknown — every setting is offered"
        )
        ui.dim(f"btop {found.version or '?'} · {keys} · {len(found.themes)} themes")
    ui.dim(f"Config: {escape(str(found.path))}")

    if found.has():
        ui.dim("Existing config — values btop would reject are shown as their defaults.")
    else:
        ui.dim("No config yet — btop writes one on first run.")

    for key, raw, why in found.invalid_existing:
        ui.warn(f"{key} = {escape(raw)}  ({escape(why)})")

    if found.keys is not None:
        if "save_config_on_exit" in found.keys:
            on = found.existing.get("save_config_on_exit", True)
            if on is True:
                ui.dim(
                    "save_config_on_exit is on — a setting changed inside btop "
                    "overwrites this file when it quits."
                )
            else:
                ui.dim("save_config_on_exit is off — this file stays authoritative.")
        else:
            ui.dim(
                "This btop has no save_config_on_exit (pre-1.4) — in-app changes "
                "always overwrite this file."
            )

    if found.running:
        ui.warn(
            f"btop is running (pid {', '.join(str(p) for p in found.running)}) — it reads "
            "this file only at start, and quitting it with in-app changes can "
            "overwrite what you save here."
        )
    for note in found.env_notes:
        ui.dim(escape(note))


def _warnings(cfg: BtopConfig, found: detect.Btop) -> None:
    if not cfg.shown_boxes.split():
        ui.warn("shown_boxes is empty — btop would show 'No boxes shown!'.")
    if cfg.update_ms < 2000:
        ui.dim("  btop recommends an update interval of 2000 ms or above.")
    if found.themes and cfg.color_theme not in found.themes:
        ui.warn(f"{escape(cfg.color_theme)} is not a theme here — btop renders Default.")
    if (
        _known(found, "save_config_on_exit")
        and cfg.save_config_on_exit
        and found.running
    ):
        ui.warn(
            "A running btop with save-on-exit can overwrite this file — quit it first."
        )


def _show_summary(cfg: BtopConfig, found: detect.Btop) -> None:
    ui.console.print()
    for label, value in render.summary(cfg, found):
        ui.console.print(f"  [bold cyan]{label:<28}[/] {escape(value)}")
    ui.console.print()
    _warnings(cfg, found)


def _show_checks(report: validate.Report, *, quiet_when_ok: bool = False) -> bool:
    if report.ok and quiet_when_ok:
        ui.dim(f"  {len(report.checks)} checks pass.")
        return True
    ui.console.print()
    ui.console.print(f"  [dim]checked with[/] [bold]btop {report.version or 'unknown'}[/]")
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        # Details carry outside text (config values, log lines, output tails) —
        # escape here at display, once, so `[/x]` can't reach Rich as markup.
        ui.console.print(
            f"  {mark} {escape(check.name)}  [dim]{escape(check.detail)}[/]"
        )
    ui.console.print()
    return report.ok


def _run_live_check(cfg: BtopConfig, found: detect.Btop, *, quiet_when_ok: bool) -> bool:
    ui.dim("  starting btop in a hidden terminal to see what it keeps (~5s)…")
    result = validate.launch(render.to_text(cfg, found), found)
    if result is None:
        ui.dim("  could not run the live check — skipped.")
        return True
    return _show_checks(validate.live_report(result, found), quiet_when_ok=quiet_when_ok)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _ask_preset(cfg: BtopConfig, found: detect.Btop) -> None:
    choices = [
        questionary.Choice(title=p.label, value=p.key, description=p.description)
        for p in PRESETS.values()
        if not (p.key == "current" and not found.has())
    ]
    detect.apply_preset(cfg, _select("Start from:", choices, cfg.preset), found)


def _ask_theme(cfg: BtopConfig, found: detect.Btop) -> None:
    if found.themes:
        choices = [
            questionary.Choice(title=name, value=name)
            for name in found.themes
        ]
        cfg.color_theme = _select("Theme:", choices, cfg.color_theme)
    else:
        cfg.color_theme = ui.text_input("Theme:", default=cfg.color_theme)
    cfg.theme_background = ui.confirm(
        "Use the theme's background colour? (off keeps terminal transparency)",
        default=cfg.theme_background,
    )
    cfg.truecolor = ui.confirm(
        "24-bit colour? (off converts to the 256-colour palette)",
        default=cfg.truecolor,
    )


def _ask_layout(cfg: BtopConfig, found: detect.Btop) -> None:
    tokens = list(BOXES)
    if found.keys is not None and "shown_gpus" in found.keys:
        tokens += list(GPU_BOXES)
    chosen = set(cfg.shown_boxes.split())
    width = max(len(t) for t in tokens) + 2
    choices = [
        questionary.Choice(
            title=f"{token:<{width}}", value=token, checked=token in chosen
        )
        for token in tokens
    ]
    picked = ui.checkbox(
        "Boxes to show:", choices=choices, instruction="(Space toggle · Enter confirm)"
    )
    cfg.shown_boxes = " ".join(t for t in tokens if t in picked)
    if _known(found, "proc_left"):
        cfg.proc_left = ui.confirm(
            "Process box on the left?", default=cfg.proc_left
        )
    if _known(found, "cpu_bottom"):
        cfg.cpu_bottom = ui.confirm(
            "CPU box at the bottom?", default=cfg.cpu_bottom
        )
    if _known(found, "mem_below_net"):
        cfg.mem_below_net = ui.confirm(
            "Memory box below the network box?", default=cfg.mem_below_net
        )


def _ask_graphs(cfg: BtopConfig, found: detect.Btop) -> None:
    setting = SETTINGS["graph_symbol"]
    ui.dim("  braille is the finest but needs a font that has it; tty is for a real console.")
    cfg.graph_symbol = _select(
        "Graph symbols:",
        [questionary.Choice(title=c, value=c) for c in setting.choices],
        cfg.graph_symbol if cfg.graph_symbol in setting.choices else setting.default,
    )
    if _known(found, "rounded_corners"):
        cfg.rounded_corners = ui.confirm(
            "Rounded box corners?", default=cfg.rounded_corners
        )


def _ask_behaviour(cfg: BtopConfig, found: detect.Btop) -> None:
    if _known(found, "update_ms"):
        ui.dim("  btop recommends 2000 ms or above for better graph samples.")
        setting = SETTINGS["update_ms"]
        while True:
            answer = ui.text_input("Update interval (ms):", default=str(cfg.update_ms))
            if answer.isdigit() and setting.minimum <= int(answer) <= setting.maximum:
                cfg.update_ms = int(answer)
                break
            ui.error(f"A whole number between {setting.minimum} and {setting.maximum}.")
    if _known(found, "vim_keys"):
        cfg.vim_keys = ui.confirm(
            "Vim keys (h,j,k,l,g,G) for lists?", default=cfg.vim_keys
        )
    if _known(found, "disable_mouse"):
        cfg.disable_mouse = ui.confirm("Disable the mouse?", default=cfg.disable_mouse)
    if _known(found, "clock_format"):
        ui.dim("  strftime plus /host /user /uptime; empty disables the clock.")
        cfg.clock_format = ui.text_input("Clock format:", default=cfg.clock_format)
    if _known(found, "background_update"):
        cfg.background_update = ui.confirm(
            "Keep updating while menus are open?", default=cfg.background_update
        )
    if _known(found, "save_config_on_exit"):
        ui.dim(
            "  With this on, settings changed inside btop overwrite this file when it "
            "quits — and a btop already running that you change something in will "
            "overwrite what you save now. Off keeps this file authoritative; in-app "
            "changes then last only for that session."
        )
        cfg.save_config_on_exit = ui.confirm(
            "Save config when btop exits?", default=cfg.save_config_on_exit
        )


def _ask_processes(cfg: BtopConfig, found: detect.Btop) -> None:
    setting = SETTINGS["proc_sorting"]
    cfg.proc_sorting = _select(
        "Sort processes by:",
        [questionary.Choice(title=c, value=c) for c in setting.choices],
        cfg.proc_sorting if cfg.proc_sorting in setting.choices else setting.default,
    )
    for key, prompt in (
        ("proc_tree", "Show processes as a tree?"),
        ("proc_per_core", "Per-core process CPU usage?"),
        ("proc_mem_bytes", "Process memory as bytes (not percent)?"),
        ("proc_cpu_graphs", "A CPU graph per process?"),
        ("proc_filter_kernel", "Hide kernel processes?"),
    ):
        if _known(found, key):
            setattr(cfg, key, ui.confirm(prompt, default=getattr(cfg, key)))


def _ask_units(cfg: BtopConfig, found: detect.Btop) -> None:
    setting = SETTINGS["temp_scale"]
    cfg.temp_scale = _select(
        "Temperature scale:",
        [questionary.Choice(title=c, value=c) for c in setting.choices],
        cfg.temp_scale if cfg.temp_scale in setting.choices else setting.default,
    )
    if _known(found, "base_10_sizes"):
        cfg.base_10_sizes = ui.confirm(
            "Base-10 sizes (KB = 1000)?", default=cfg.base_10_sizes
        )


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak.{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, dest)
    return dest


def save(cfg: BtopConfig, found: detect.Btop, path: Path) -> tuple[Path, Path | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = backup(path)
    path.write_text(render.to_text(cfg, found), encoding="utf-8")
    return path, saved


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for piece in version.split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    return tuple(parts)


# ---------------------------------------------------------------------------
# The capability offer — after a real save only
# ---------------------------------------------------------------------------


def _capability(found: detect.Btop) -> None:
    path = capability.binary() or found.binary
    if not path:
        return
    setcap = capability.tool("setcap") or "setcap"
    grant_cmd = f"sudo {setcap} cap_perfmon=+ep {path}"

    why = capability.refusal(path)
    if why is not None:
        ui.dim(f"cap_perfmon is not offered: {escape(why)}")
        ui.dim(f"  to grant it yourself:  {escape(grant_cmd)}")
        return

    has = capability.has_perfmon(path)
    if has is None:
        ui.dim("Could not tell whether cap_perfmon is set on the btop binary.")
        ui.dim(f"  grant it yourself with:  {escape(grant_cmd)}")
        return
    if has:
        ui.dim("cap_perfmon is already set — CPU watts and Intel GPU stats can be read.")
        if ui.confirm("Remove the capability?", default=False):
            try:
                capability.revoke(path)
            except RuntimeError as exc:
                ui.error(f"Could not remove it: {escape(str(exc))}")
            else:
                ui.success("cap_perfmon removed.")
        return

    ui.console.print()
    ui.dim("btop needs the cap_perfmon capability for CPU package watts and Intel GPU")
    ui.dim("stats — upstream's `make setcap`, preferred over setuid. It applies to every")
    ui.dim("user who runs this binary, and an apt upgrade replaces the binary and drops")
    ui.dim("it — re-run this wizard then. The grant is verified with getcap; whether")
    ui.dim("watts then appear is not (containers and VMs often hide power counters).")
    # questionary prompts are not Rich markup — escaping would print a literal `\[`.
    if not ui.confirm(f"Run `{grant_cmd}`?", default=False):
        return
    try:
        capability.grant(path)
    except RuntimeError as exc:
        ui.error(f"Could not set the capability: {escape(str(exc))}")
    else:
        ui.success("cap_perfmon set — verified with getcap.")


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------


_MENU = {
    "save": "Save this configuration",
    "preset": "Start from a different preset",
    "theme": "Theme, background and colour depth",
    "layout": "Which boxes show and where",
    "graphs": "Graph glyphs and corners",
    "behaviour": "Update rate, keys, clock and save-on-exit",
    "processes": "Process list: sorting, tree, graphs",
    "units": "Temperature and size units",
    "check": "Check this against btop",
    "text": "Show the generated config file",
    "cancel": "Cancel without saving",
}


def run(*, target: Path | None = None) -> BtopConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled."""
    found = detect.inspect(on_slow=lambda msg: ui.dim(f"  {msg}"))
    cfg = detect.suggest(found)  # also fills found.invalid_existing
    if target is not None:
        cfg.target = target

    ui.section("Configure btop")
    ui.dim("btop rewrites btop.conf with its own comments whenever it saves — this")
    ui.dim("wizard edits around that. Nothing is written until you save.")
    ui.console.print()
    _report(found)

    ui.console.print()
    _ask_preset(cfg, found)

    while True:
        _show_summary(cfg, found)
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
            ui.code_block(render.to_text(cfg, found), language="ini")
            continue
        if action == "check":
            _show_checks(validate.verify(cfg, found))
            if found.installed:
                _run_live_check(cfg, found, quiet_when_ok=False)
            continue
        if action == "preset":
            _ask_preset(cfg, found)
        elif action == "theme":
            _ask_theme(cfg, found)
        elif action == "layout":
            _ask_layout(cfg, found)
        elif action == "graphs":
            _ask_graphs(cfg, found)
        elif action == "behaviour":
            _ask_behaviour(cfg, found)
        elif action == "processes":
            _ask_processes(cfg, found)
        elif action == "units":
            _ask_units(cfg, found)

    path = target or cfg.target
    new_text = render.to_text(cfg, found)

    ok = _show_checks(validate.verify(cfg, found), quiet_when_ok=True)
    if found.installed:
        ok = _run_live_check(cfg, found, quiet_when_ok=True) and ok
    if not ok and not ui.confirm("Some checks failed. Save anyway?", default=False):
        ui.dim("Nothing was written.")
        return None

    do_write = True
    if path.exists():
        old_text = path.read_text(encoding="utf-8", errors="replace")
        if old_text == new_text:
            do_write = False
            ui.console.print()
            ui.dim("The file already says that — nothing to write.")
        elif target is None:
            # No reliable generated-by marker exists: btop rewrites the file on
            # every save, so an existing file always gets the diff + confirm.
            ui.console.print()
            lines = render.diff(old_text, new_text)
            if lines:
                ui.code_block("\n".join(lines), language="diff")
            ui.dim("  A timestamped backup is kept.")
            if not ui.confirm(f"Overwrite {path}?", default=False):
                ui.dim("Cancelled — nothing was written.")
                return None

    if do_write:
        written, saved_backup = save(cfg, found, path)
        ui.console.print()
        ui.success(f"Saved {escape(str(written))}")
        if saved_backup:
            ui.dim(f"  Previous version backed up to {escape(saved_backup.name)}")

    _after_save(cfg, found, path, target)
    _capability_if(found, target)
    return cfg


def _capability_if(found: detect.Btop, target: Path | None) -> None:
    """The grant is a system-wide property change — never under `--output`, and
    only when a real binary is there to receive it."""
    if target is None and found.installed:
        _capability(found)


def _after_save(cfg: BtopConfig, found: detect.Btop, written: Path, target) -> None:
    if found.running:
        ui.warn(
            "Running btop processes read the config only at start — restart them."
        )
        ui.dim(
            "  And if one was started with save-on-exit, changing a setting inside "
            "it before quitting overwrites this file."
        )
    if target is not None:
        ui.dim(f"  Copy it to {escape(str(found.path))} to use it.")
        if _version_tuple(found.version) >= (1, 4):
            ui.dim(f"  Or try it directly:  btop -c {escape(str(written))}")
        # 1.3.0 has no -c flag, so no try-it hint is possible there.
    ui.console.print()
    ui.dim(f"Re-run any time:  devstuff configure btop   ·   edit: {escape(str(written))}")
    ui.console.print()


__all__ = ["backup", "config_path", "run", "save"]
