"""The interactive `devstuff configure proxmox` flow.

Three things about the shape:

- **There is no tool to configure.** This wizard describes devstuff's access to a machine
  somewhere else, so it is registered `standalone=True` and the install-state gate is
  skipped rather than reporting a `proxmox` package that was never meant to exist.
- **Nothing is written until the save**, secrets included: a secret typed into the wizard
  is held in memory and lands in its `0600` file only when the profile file does.
- **The connection test is the functions' own transport** (`validate.selftest` ->
  `lib.sh`), so a passing test is the thing itself passing, not an approximation of it.
"""

from __future__ import annotations

import os
from pathlib import Path

import questionary
from rich.table import Table

from dev_setup import ui
from dev_setup.catalog import CatalogError
from dev_setup.configure.proxmox import detect, render, validate
from dev_setup.configure.proxmox.model import (
    ASSUME_YES_ENV,
    BACKUP_MODES,
    CONFIG_PATH,
    GROUPS,
    OPERATIONS,
    PROFILE_ENV,
    SSH_AUTH,
    TOKEN_ID_RE,
    Profile,
    ProxmoxConfig,
)

DEFAULT_KEYS = ("id_ed25519", "id_ecdsa", "id_rsa")


def config_path() -> Path:
    return CONFIG_PATH


# ---------------------------------------------------------------------------
# The shell bridge (`devstuff configure proxmox --export [PROFILE]`)
# ---------------------------------------------------------------------------


def export(arg: str | None = None) -> str:
    """Shell assignments for one profile — what every pve-* function starts with.

    Returns text; prints nothing. `configure_cmd` puts it on stdout verbatim, so a
    stray print here would land in the caller's `eval` (FR-23).
    """
    cfg = detect.load_config()
    wanted = arg or os.environ.get(PROFILE_ENV) or ""
    profile = cfg.get(wanted or None)
    return render.export_shell(profile)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _default_identity() -> str:
    for name in DEFAULT_KEYS:
        candidate = Path.home() / ".ssh" / name
        if candidate.exists():
            return str(candidate)
    return str(Path.home() / ".ssh" / DEFAULT_KEYS[0])


def _ask_secret(profile: Profile, pending: dict[str, str], *, label: str) -> None:
    """Where the secret lives — never what it is, in the file we write (FR-5)."""
    ui.console.print()
    ui.dim(f"  The {label} is not stored in proxmox.yaml. Choose where it should live:")

    keep = bool(profile.secret_file and render.secret_path(profile.name).exists())
    choices = []
    if keep:
        choices.append(questionary.Choice(
            title="keep the one already stored",
            value="keep",
            description=str(render.secret_path(profile.name)),
        ))
    choices += [
        questionary.Choice(
            title="a file only you can read",
            value="file",
            description=f"written to {render.secret_path(profile.name or 'NAME')}, mode 0600",
        ),
        questionary.Choice(
            title="an environment variable",
            value="env",
            description="nothing touches the disk; you set it in your shell or a secrets manager",
        ),
    ]
    where = ui.select(f"Where does the {label} live?", choices) or ("keep" if keep else "file")

    if where == "keep":
        return
    if where == "env":
        suggestion = profile.secret_env or f"PVE_SECRET_{profile.name.upper().replace('-', '_')}"
        name = ui.text_input("Environment variable name:", default=suggestion, required=True)
        profile.secret_env = name.strip()
        profile.secret_file = ""
        pending.pop(profile.name, None)
        if not os.environ.get(profile.secret_env):
            ui.warn(f"${profile.secret_env} is not set in this shell yet.")
            ui.dim(f"  Set it before running a pve-* function:  export {profile.secret_env}=...")
        return

    value = ui.password(f"{label.capitalize()}:")
    if not value:
        ui.warn("Nothing entered — the secret reference was left as it was.")
        return
    pending[profile.name] = value
    profile.secret_file = str(render.secret_path(profile.name))
    profile.secret_env = ""


def _ask_ssh(profile: Profile, pending: dict[str, str]) -> None:
    profile.user = ui.text_input("User on the node:", default=profile.user or "root").strip()
    port = ui.text_input("SSH port:", default=str(profile.port)).strip()
    profile.port = int(port) if port.isdigit() else 22

    profile.auth = ui.select(
        "How do you authenticate?",
        [
            questionary.Choice(
                title="ssh-agent", value="agent",
                description="whatever key your agent already holds",
            ),
            questionary.Choice(
                title="a key file", value="key",
                description="a specific private key, passed as ssh -i",
            ),
            questionary.Choice(
                title="a password", value="password",
                description="needs sshpass; a key is better in every way",
            ),
        ],
        default=next((c for c in SSH_AUTH if c == profile.auth), "agent"),
    ) or "agent"

    if profile.auth == "key":
        profile.identity_file = ui.text_input(
            "Private key:", default=profile.identity_file or _default_identity(), required=True
        ).strip()
        if not Path(profile.identity_file).expanduser().exists():
            ui.warn(f"{profile.identity_file} does not exist yet.")
    else:
        profile.identity_file = ""

    if profile.auth == "password":
        ui.console.print()
        ui.warn("Password authentication is the weakest option here.")
        ui.dim("  It needs sshpass, and the password is handed to it through the environment")
        ui.dim("  of a child process — readable by you and by root, not by other users.")
        ui.dim("  It also means the host key must already be known, since there is no way to")
        ui.dim("  answer a host-key prompt from behind sshpass.")
        ui.dim("  Copy a key across instead if you can:  ssh-copy-id "
               f"{profile.user}@{profile.host or 'node'}")
        _ask_secret(profile, pending, label="password")
    else:
        profile.secret_env = ""
        profile.secret_file = ""
        pending.pop(profile.name, None)

    if profile.user != "root":
        ui.console.print()
        ui.dim("  qm, pct and vzdump all need root on the node.")
        profile.sudo = ui.confirm("Run remote commands through sudo -n?", default=True)
    else:
        profile.sudo = False


def _token_help(profile: Profile) -> None:
    """What to type on the node to create a token this will work with.

    The privileges are the ones the API schema actually requires — including the one
    nobody expects, which is that listing pending updates needs Sys.Modify.
    """
    token = profile.token_id or "root@pam!devstuff"
    user, _, name = token.partition("!")
    ui.console.print()
    ui.dim("  Create the token on the node (as root):")
    ui.code_block(
        f"pveum user token add {user} {name or 'devstuff'} --privsep 1\n"
        f"pveum acl modify / --tokens '{token}' --roles PVEAuditor\n"
        "\n"
        "# pve-updates additionally needs Sys.Modify — listing pending updates is\n"
        "# guarded as a write by Proxmox, which PVEAuditor does not grant:\n"
        "pveum role add DevstuffUpdates -privs 'Sys.Modify'\n"
        f"pveum acl modify /nodes --tokens '{token}' --roles DevstuffUpdates",
    )
    ui.dim("  `pveum user token add` prints the secret once and never again.")


def _ask_api(profile: Profile, pending: dict[str, str]) -> None:
    port = ui.text_input("API port:", default=str(profile.api_port)).strip()
    profile.api_port = int(port) if port.isdigit() else 8006

    while True:
        profile.token_id = ui.text_input(
            "API token ID (USER@REALM!TOKENNAME):",
            default=profile.token_id or "root@pam!devstuff",
            required=True,
        ).strip()
        if TOKEN_ID_RE.match(profile.token_id):
            break
        ui.error("That does not look like a token ID. It should read like root@pam!devstuff.")

    profile.verify_tls = ui.confirm("Verify the node's TLS certificate?", default=profile.verify_tls)
    if not profile.verify_tls:
        ui.warn("Certificate verification is off for this profile.")
        ui.dim("  A default Proxmox install serves a self-signed certificate, so this is the")
        ui.dim("  usual answer — but it means nothing detects an intercepted connection.")

    _token_help(profile)
    _ask_secret(profile, pending, label="token secret")


def _ask_profile(profile: Profile, pending: dict[str, str]) -> Profile | None:
    ui.console.print()
    profile.transport = ui.select(
        "How should devstuff reach this Proxmox?",
        [
            questionary.Choice(
                title="SSH", value="ssh",
                description="everything, including the commands that change state",
            ),
            questionary.Choice(
                title="API token", value="api",
                description="read-only here: inventory, health, storage, backups",
            ),
        ],
        default=profile.transport,
    ) or "ssh"

    profile.host = ui.text_input(
        "Hostname or IP:", default=profile.host, required=True
    ).strip()
    profile.node = ui.text_input(
        "Node name in the cluster (blank to detect it):", default=profile.node
    ).strip()
    profile.description = ui.text_input(
        "Description (optional):", default=profile.description
    ).strip()

    if profile.transport == "ssh":
        _ask_ssh(profile, pending)
    else:
        _ask_api(profile, pending)
    return profile


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _profiles_table(cfg: ProxmoxConfig) -> None:
    if not cfg.profiles:
        ui.dim("  No profiles yet.")
        return
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("  profile")
    table.add_column("via")
    table.add_column("endpoint")
    table.add_column("auth")
    table.add_column("secret")
    for name, profile in cfg.profiles.items():
        state, detail = detect.secret_state(profile)
        mark = {"ok": "[green]ok[/]", "n/a": "[dim]—[/]", "insecure": "[yellow]loose[/]"}.get(
            state, "[red]missing[/]"
        )
        label = f"[bold]{name}[/]" + ("  [dim](default)[/]" if name == cfg.default else "")
        auth = profile.auth if profile.transport == "ssh" else profile.token_id
        table.add_row(f"  {label}", profile.transport, profile.endpoint(), auth,
                      f"{mark} [dim]{detail}[/]")
    ui.console.print(table)


def _show_operations(cfg: ProxmoxConfig) -> None:
    """What these profiles let you run — the point of the whole exercise."""
    has_ssh = any(p.transport == "ssh" for p in cfg.profiles.values())

    def invocation(op) -> str:
        args = " ".join(f"<{p}>" for p in op.params if p != "profile")
        return f"devstuff run {op.key}{' ' + args if args else ''}"

    # Width from the longest line rather than a constant, or the two commands with four
    # parameters knock the descriptions out of their column.
    width = max(len(invocation(op)) for op in OPERATIONS.values()) + 2

    ui.console.print()
    for group, heading in GROUPS.items():
        ops = [op for op in OPERATIONS.values() if op.group == group]
        if not ops:
            continue
        ui.console.print(f"  [bold]{heading}[/]")
        for op in ops:
            command = invocation(op)
            note = ""
            if op.writes:
                note = "[dim] — ssh only, asks first[/]" if has_ssh else "[yellow] — needs an ssh profile[/]"
            ui.console.print(f"    [cyan]{command:<{width}}[/] {op.summary}{note}")
        ui.console.print()
    ui.dim(f"  A profile other than the default:  devstuff run pve-guests '' <profile>"
           f"   ·   or export {PROFILE_ENV}")
    ui.dim(f"  Approve a state change without a terminal:  {ASSUME_YES_ENV}=1")
    ui.dim(f"  Backup modes: {', '.join(BACKUP_MODES)}")
    ui.console.print()


def _report(found: detect.Found) -> None:
    if found.error:
        ui.warn(f"{found.path} could not be read as a profile file:")
        ui.dim(f"  {found.error}")
        ui.dim("  Saving from here replaces it; a timestamped backup is kept.")
    elif found.exists:
        ui.dim(f"Profiles: {found.path}"
               + ("" if found.generated else "  (not written by this wizard)"))
    else:
        ui.dim(f"No profiles yet — this writes {found.path}.")

    missing = found.missing_tools()
    for name in missing:
        # sshpass only matters for password auth, so its absence is not worth a warning
        # until someone chooses that.
        if name == "sshpass":
            continue
        ui.warn(f"{name} is not installed — profiles using it cannot connect.")
        ui.dim(f"  {detect.CLIENT_TOOLS[name]}")


def _run_test(cfg: ProxmoxConfig, name: str) -> None:
    profile = cfg.profiles[name]
    ui.console.print()
    ui.dim(f"  Running the same transport the pve-* functions use, against {profile.endpoint()}.")
    with ui.spinner(f"Checking {name}…"):
        report = validate.selftest(profile)
    ui.console.print()
    if report.error:
        ui.error(report.error)
        ui.console.print()
        return
    for check in report.checks:
        mark = {"ok": "[green]✔[/]", "warn": "[yellow]![/]"}.get(check.status, "[red]✖[/]")
        ui.console.print(f"  {mark} {check.name:<16} [dim]{check.detail}[/]")
    discovered = report.node_name()
    if discovered and not profile.node:
        profile.node = discovered
        ui.console.print()
        ui.success(f"Recorded the node name: {discovered}")
    ui.console.print()
    if report.ok:
        ui.success(f"{name} works.")
    else:
        ui.warn(f"{name} is not usable yet — see the failures above.")
    ui.console.print()


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save these profiles",
    "add": "Add a profile",
    "edit": "Edit a profile",
    "test": "Test a connection",
    "default": "Choose the default profile",
    "remove": "Remove a profile",
    "commands": "Show what these profiles let you run",
    "yaml": "Show the generated proxmox.yaml",
    "cancel": "Cancel without saving",
}


def _pick_profile(cfg: ProxmoxConfig, prompt: str) -> str | None:
    if not cfg.profiles:
        ui.warn("There are no profiles yet.")
        return None
    if len(cfg.profiles) == 1:
        return next(iter(cfg.profiles))
    return ui.select(prompt, [questionary.Choice(title=n, value=n) for n in cfg.profiles]) or None


def _unique_name(cfg: ProxmoxConfig) -> str | None:
    while True:
        name = ui.text_input("Name for this profile:", default="", required=True).strip()
        if name in cfg.profiles:
            ui.error(f"There is already a profile called {name!r}.")
            continue
        return name


def run(*, target: Path | None = None) -> ProxmoxConfig | None:
    found = detect.inspect()
    cfg = found.config
    if target is not None:
        cfg.target = target

    ui.section("Configure Proxmox")
    ui.dim("Connection profiles for the pve-* functions. Nothing is written until you save.")
    ui.dim("Ctrl-C to bail out.")
    ui.console.print()
    _report(found)

    # Secrets typed into the wizard, held until the save so that cancelling really does
    # leave the disk untouched.
    pending: dict[str, str] = {}

    if not cfg.profiles:
        ui.console.print()
        name = _unique_name(cfg)
        if name is None:
            return None
        profile = _ask_profile(Profile(name=name), pending)
        if profile is None:
            return None
        cfg.profiles[name] = profile
        cfg.default = name

    while True:
        ui.console.print()
        _profiles_table(cfg)
        ui.console.print()
        action = ui.select(
            "Looks good?",
            [questionary.Choice(title=label, value=key) for key, label in _MENU.items()],
            default="save",
        )
        if action in ("save", ""):
            break
        if action == "cancel":
            ui.dim("Cancelled — nothing was written.")
            return None
        if action == "add":
            name = _unique_name(cfg)
            if name:
                cfg.profiles[name] = _ask_profile(Profile(name=name), pending)
                if not cfg.default:
                    cfg.default = name
        elif action == "edit":
            name = _pick_profile(cfg, "Edit which profile?")
            if name:
                _ask_profile(cfg.profiles[name], pending)
        elif action == "test":
            name = _pick_profile(cfg, "Test which profile?")
            if name:
                _run_test(cfg, name)
        elif action == "default":
            name = _pick_profile(cfg, "Which profile should be the default?")
            if name:
                cfg.default = name
        elif action == "remove":
            name = _pick_profile(cfg, "Remove which profile?")
            if name and ui.confirm(f"Remove {name!r}?", default=False):
                del cfg.profiles[name]
                pending.pop(name, None)
                if cfg.default == name:
                    cfg.default = next(iter(cfg.profiles), "")
        elif action == "commands":
            _show_operations(cfg)
        elif action == "yaml":
            ui.code_block(render.to_yaml(cfg), language="yaml")

    return _save(cfg, found, pending, target=target)


def _save(
    cfg: ProxmoxConfig,
    found: detect.Found,
    pending: dict[str, str],
    *,
    target: Path | None,
) -> ProxmoxConfig | None:
    path = target or cfg.target
    text = render.to_yaml(cfg)

    # The emitter writes YAML by hand for the comments; this is what proves the two
    # representations still agree.
    if not render.matches(text, cfg):
        ui.error("The generated file does not read back as what you configured.")
        ui.dim("  Nothing was written. This is a devstuff bug — please report it.")
        return None

    if path.exists() and not found.generated and target is None:
        ui.console.print()
        ui.warn(f"{path} was not written by this wizard — it will be replaced.")
        lines = render.diff(found.text, text)
        if lines:
            ui.code_block("\n".join(lines), language="diff")
        ui.dim("  A timestamped backup is kept.")
        if not ui.confirm("Overwrite it?", default=False):
            ui.dim("Cancelled — nothing was written.")
            return None

    written, saved_backup = render.save(cfg, path)
    ui.console.print()
    ui.success(f"Saved {written}")
    if saved_backup:
        ui.dim(f"  Previous version backed up to {saved_backup.name}")

    if target is not None:
        if pending:
            ui.warn("Secrets were not written: --output is an export, not a live change.")
        return cfg

    for name, secret in pending.items():
        if name not in cfg.profiles:
            continue
        try:
            secret_file = render.write_secret(name, secret)
        except OSError as exc:
            ui.error(f"Could not write the secret for {name!r}: {exc}")
            continue
        ui.dim(f"  Secret for {name} written to {secret_file} (0600)")

    try:
        render.load(written.read_text(encoding="utf-8"), source=written)
    except CatalogError as exc:  # pragma: no cover — matches() already checked this
        ui.error(f"The saved file does not load: {exc}")
        return None

    ui.console.print()
    ui.dim("Try it:  devstuff run pve-guests   ·   every command:  devstuff functions list")
    ui.dim(f"Re-run any time:  devstuff configure proxmox   ·   edit: {written}")
    ui.console.print()
    return cfg


__all__ = ["config_path", "export", "run"]
