"""The interactive `devstuff configure ansible` flow.

Shape follows the others: look at the project, offer a preset, then a review loop.
The distinctive step is the check — this wizard can ask ansible what it *read* from
the candidate, not merely whether it parsed, so "ansible reads every setting" is a
line on the review screen rather than an assumption.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import questionary
from rich.table import Table

from dev_setup import ui
from dev_setup.configure.ansible import detect, render, validate
from dev_setup.configure.ansible.model import (
    CONFIG_FILE,
    PRESETS,
    SETTINGS,
    STDOUT_CALLBACKS,
    AnsibleConfig,
)


def config_path() -> Path:
    """The ansible.cfg for this directory.

    Deliberately the project-local one rather than whatever `ansible-config` reports
    as active: `--path` is asking where this wizard would write, and it writes here.
    The active file is reported separately in the wizard, since they can differ.
    """
    return Path.cwd() / CONFIG_FILE


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _select(prompt: str, choices: list, current: object) -> str:
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or str(current)


def _ask_preset(cfg: AnsibleConfig, found: detect.Project) -> None:
    choices = [
        questionary.Choice(title=preset.label, value=preset.key, description=preset.description)
        for preset in PRESETS.values()
        if not (preset.key == "current" and not found.has())
    ]
    detect.apply_preset(cfg, _select("Start from:", choices, cfg.preset), found)


def _ask_layout(cfg: AnsibleConfig, found: detect.Project) -> None:
    ui.console.print()
    ui.dim("  Relative paths resolve against the directory ansible runs in. Blank leaves")
    ui.dim("  ansible's own default alone.")
    if found.has_inventory:
        ui.dim("  An inventory directory exists here.")
    cfg.inventory = ui.text_input("Inventory:", default=cfg.inventory)
    cfg.roles_path = ui.text_input("Roles path (blank for ansible's default):", default=cfg.roles_path)
    cfg.collections_path = ui.text_input(
        "Collections path (blank for ansible's default):", default=cfg.collections_path
    )


def _ask_connection(cfg: AnsibleConfig) -> None:
    cfg.host_key_checking = ui.confirm(
        "Check SSH host keys? (off makes unattended runs work, and MITM undetectable)",
        default=cfg.host_key_checking,
    )
    cfg.remote_user = ui.text_input(
        "Default remote user (blank to leave it to the inventory):", default=cfg.remote_user
    )
    cfg.private_key_file = ui.text_input(
        "Default private key file (blank for none):", default=cfg.private_key_file
    )
    while True:
        answer = ui.text_input("Connection timeout in seconds:", default=str(cfg.timeout))
        if answer.isdigit() and int(answer) >= 1:
            cfg.timeout = int(answer)
            break
        ui.error("A whole number, 1 or more.")


def _ask_speed(cfg: AnsibleConfig) -> None:
    ui.console.print()
    ui.dim("  ansible's default of 5 forks is the usual reason a large run feels slow.")
    while True:
        answer = ui.text_input("Hosts to run against in parallel:", default=str(cfg.forks))
        if answer.isdigit() and int(answer) >= 1:
            cfg.forks = int(answer)
            break
        ui.error("A whole number, 1 or more.")

    cfg.pipelining = ui.confirm(
        "Enable pipelining? (roughly halves SSH round trips per task)",
        default=cfg.pipelining,
    )
    if cfg.pipelining:
        ui.dim("  Needs `requiretty` off in sudoers on the targets — the modern default.")

    setting = SETTINGS["gathering"]
    cfg.gathering = _select(
        "Fact gathering:",
        [
            questionary.Choice(
                title=choice,
                value=choice,
                description={
                    "implicit": "Gather for every play unless the play says otherwise",
                    "explicit": "Never gather unless a play asks",
                    "smart": "Gather once per host per run",
                }[choice],
            )
            for choice in setting.choices
        ],
        cfg.gathering,
    )


def _ask_output(cfg: AnsibleConfig, found: detect.Project) -> None:
    choices = []
    for name, description in STDOUT_CALLBACKS.items():
        missing = bool(found.callbacks) and name not in found.callbacks
        choices.append(questionary.Choice(
            title=name + ("  (not available)" if missing else ""),
            value=name,
            description=description,
        ))
    cfg.stdout_callback = _select("Output style:", choices, cfg.stdout_callback)

    # The modern answer to "make the output readable". The old one,
    # `stdout_callback = yaml`, names a plugin that was removed and silently does
    # nothing — so this is offered explicitly, with its one wart stated.
    cfg.callback_result_format = _select(
        "Print task results as:",
        [
            questionary.Choice(
                title="json", value="json", description="ansible's default"
            ),
            questionary.Choice(
                title="yaml",
                value="yaml",
                description=(
                    "Readable multi-line output. `ansible-config validate` will call "
                    "this key unknown — it only knows core settings, and this is a "
                    "callback-plugin one. It works."
                ),
            ),
        ],
        cfg.callback_result_format,
    )

    cfg.display_skipped_hosts = ui.confirm(
        "Print a line for every skipped task?", default=cfg.display_skipped_hosts
    )
    cfg.nocows = ui.confirm("Disable cowsay?", default=cfg.nocows)
    cfg.log_path = ui.text_input("Log every run to a file (blank for none):", default=cfg.log_path)
    if cfg.log_path:
        ui.warn("Ansible never rotates this file, and failed tasks can leak secrets into it.")


def _ask_become(cfg: AnsibleConfig) -> None:
    cfg.become = ui.confirm(
        "Escalate privileges for every task by default?", default=cfg.become
    )
    if not cfg.become:
        return
    ui.warn("Every task will run as root unless a play says `become: false`.")
    cfg.become_method = _select(
        "How to escalate:",
        [questionary.Choice(title=m, value=m) for m in SETTINGS["become_method"].choices],
        cfg.become_method,
    )
    cfg.become_user = ui.text_input("Become which user:", default=cfg.become_user)
    cfg.become_ask_pass = ui.confirm(
        "Prompt for the escalation password?", default=cfg.become_ask_pass
    )


def _ask_vault(cfg: AnsibleConfig, found: detect.Project) -> None:
    ui.console.print()
    ui.dim("  A file holding the vault password, or an executable that prints it —")
    ui.dim("  which is how the password comes out of a keychain rather than off disk.")
    if found.has_vault_file:
        ui.dim("  A vault password file already exists here.")
    cfg.vault_password_file = ui.text_input(
        "Vault password file (blank for none):", default=cfg.vault_password_file
    )
    if cfg.vault_password_file:
        ui.warn("Keep it out of git and chmod 600 it.")


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _show_preview(cfg: AnsibleConfig, found: detect.Project) -> None:
    ui.console.print()
    for label, value in render.summary(cfg):
        ui.console.print(f"  [bold cyan]{label:<16}[/] {value}")

    rows = render.setting_rows(cfg)
    ui.console.print()
    if not rows:
        ui.dim("  Nothing is set — this writes an empty config and every ansible default applies.")
        ui.console.print()
        return

    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("  section")
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_column("note")
    for section, key, value, why in rows:
        table.add_row(f"  [dim][{section}][/]", key, value, f"[dim]{why}[/]")
    ui.console.print(table)

    for warning in cfg.warnings():
        ui.console.print()
        ui.warn(warning)

    if detect.unknown_callback(cfg, found):
        ui.console.print()
        ui.warn(
            f"'{cfg.stdout_callback}' is not a callback this ansible can load — "
            "the run would fall back to the default."
        )
    ui.console.print()


def _run_check(cfg: AnsibleConfig, *, quiet_when_ok: bool = False) -> bool:
    with ui.spinner("Asking ansible what it makes of this…"):
        report = validate.verify(cfg)
    if report.ok and quiet_when_ok:
        ui.dim(f"  {len(report.checks)} checks pass.")
        return True
    ui.console.print()
    ui.console.print(f"  [dim]checked with[/] [bold]ansible-core {report.version}[/]")
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        ui.console.print(f"  {mark} {check.name}  [dim]{check.detail}[/]")
    ui.console.print()
    return report.ok


def _show_dump(cfg: AnsibleConfig) -> None:
    """What ansible actually reads from this file — the thing validation does not say."""
    if not validate.available():
        ui.warn("ansible is not installed, so it cannot be asked what it reads.")
        return
    with ui.spinner("Running ansible-config dump against the candidate…"):
        got = validate.dump(cfg)
    if got is None:
        ui.warn("ansible-config dump did not run.")
        return
    ui.console.print()
    ui.dim("  Everything ansible reads from this file:")
    ui.console.print()
    for name, (value, _) in sorted(got.items()):
        ui.console.print(f"  [bold cyan]{name:<32}[/] {value}")
    expected = cfg.expected_env()
    missing = [name for name in expected if name not in got]
    ui.console.print()
    if missing:
        for name in missing:
            setting = expected[name]
            ui.error(f"{setting.section}.{setting.ini_key} was written but ansible did not read it.")
    else:
        ui.success(f"All {len(expected)} settings written are settings ansible reads.")
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


def save(cfg: AnsibleConfig, path: Path) -> tuple[Path, Path | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = backup(path)
    path.write_text(render.to_text(cfg), encoding="utf-8")
    return path, saved


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save this configuration",
    "preset": "Start from a different preset",
    "layout": "Change inventory, roles and collections paths",
    "connection": "Change how ansible connects",
    "speed": "Change forks, pipelining and fact gathering",
    "output": "Change output style and logging",
    "become": "Change privilege escalation",
    "vault": "Change the vault password file",
    "check": "Check this against ansible",
    "dump": "Show what ansible actually reads from it",
    "text": "Show the generated ansible.cfg",
    "cancel": "Cancel without saving",
}


def _report(found: detect.Project) -> None:
    if not found.installed:
        ui.warn("ansible is not installed — the wizard can still write a config,")
        ui.warn("but it cannot check what ansible makes of it.")
        ui.dim("  Install it with:  devstuff install ansible")
    else:
        ui.dim(f"ansible-core {found.version} · {len(found.callbacks)} callback plugins")

    if found.world_writable:
        # The quietest failure ansible has: the file exists, parses, and is ignored.
        ui.warn(
            f"{found.root} is world-writable, so ansible ignores an ansible.cfg here "
            "entirely."
        )
        ui.dim("  chmod o-w . — otherwise nothing written here will ever be read.")

    if found.env_override:
        ui.warn(f"ANSIBLE_CONFIG={found.env_override} overrides every file-based config.")
    elif found.active_config and found.active_config != found.path:
        ui.dim(f"Currently in force: {found.active_config}")
    elif not found.active_config:
        ui.dim("No config is in force here — ansible is running on its built-in defaults.")

    if found.has():
        ui.dim(
            f"Existing config: {found.path.name} "
            f"({sum(len(b) for b in found.existing.values())} settings across "
            f"{len(found.existing)} sections"
            + (", written by this wizard" if found.generated else "")
            + ")"
        )
        if not found.parse_ok:
            ui.warn("The existing file does not parse — ansible cannot be reading it.")
    else:
        ui.dim(f"No {CONFIG_FILE} here yet.")

    bits = []
    if found.playbooks:
        bits.append(f"{len(found.playbooks)} playbooks")
    if found.has_inventory:
        bits.append("inventory/")
    if found.has_roles:
        bits.append("roles/")
    if found.has_collections:
        bits.append("collections/")
    if found.has_vault_file:
        bits.append("a vault password file")
    if bits:
        ui.dim("Found here: " + ", ".join(bits))


def run(*, target: Path | None = None) -> AnsibleConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled."""
    found = detect.inspect()
    cfg = detect.suggest(found)
    if target is not None:
        cfg.target = target

    ui.section("Configure ansible")
    ui.dim("Pick a starting point, adjust it, and see what ansible actually reads from")
    ui.dim("the result before anything is written. Ctrl-C to bail out.")
    ui.console.print()
    _report(found)

    ui.console.print()
    _ask_preset(cfg, found)

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
            _run_check(cfg)
            continue
        if action == "dump":
            _show_dump(cfg)
            continue
        if action == "preset":
            _ask_preset(cfg, found)
        elif action == "layout":
            _ask_layout(cfg, found)
        elif action == "connection":
            _ask_connection(cfg)
        elif action == "speed":
            _ask_speed(cfg)
        elif action == "output":
            _ask_output(cfg, found)
        elif action == "become":
            _ask_become(cfg)
        elif action == "vault":
            _ask_vault(cfg, found)

    path = target or cfg.target

    if not _run_check(cfg, quiet_when_ok=True) and not ui.confirm(
        "Some checks failed. Save anyway?", default=False
    ):
        ui.dim("Nothing was written.")
        return None

    if path.exists() and not found.generated:
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

    if found.world_writable and target is None:
        ui.warn("Remember: this directory is world-writable, so ansible will ignore it.")

    if cfg.vault_password_file and not Path(cfg.vault_password_file).exists():
        ui.console.print()
        ui.dim(f"  {cfg.vault_password_file} does not exist yet. Create it with:")
        ui.dim(f"    printf '%s' 'your-password' > {cfg.vault_password_file}")
        ui.dim(f"    chmod 600 {cfg.vault_password_file}")
        ui.dim(f"    echo '{cfg.vault_password_file}' >> .gitignore")

    ui.console.print()
    ui.dim("See everything ansible read:  ansible-config dump --only-changed")
    ui.dim("Every option this ansible has: ansible-config init --disabled")
    ui.dim(f"Re-run any time:  devstuff configure ansible   ·   edit: {written}")
    ui.console.print()
    return cfg


__all__ = ["backup", "config_path", "run", "save"]
