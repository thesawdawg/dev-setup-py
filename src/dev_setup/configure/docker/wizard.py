"""The interactive `devstuff configure docker` flow.

Shape follows the other configurators — look at the machine, offer a preset that
matches it, then a review loop where every answer can be revisited. Two things are
different, both because this config is not the user's own file:

- Writing goes through `sudo install`, shown before it runs.
- Writing changes nothing until the daemon restarts, and restarting stops running
  containers unless live-restore was *already* on. So the restart is its own
  question, asked after the save, with the container count in it.
"""

from __future__ import annotations

from pathlib import Path

import questionary
from rich.table import Table

from dev_setup import ui
from dev_setup.configure.docker import detect, render, validate
from dev_setup.configure.docker.model import (
    BUILTIN_DRIVERS,
    DEFAULT_MAX_FILE,
    DEFAULT_MAX_SIZE,
    LOG_DRIVERS,
    NEEDS_ENDPOINT,
    PRESETS,
    SETTINGS,
    TOGGLE_SETTINGS,
    DockerConfig,
    valid_size,
)


def config_path() -> Path:
    """The daemon.json the daemon on this machine actually reads.

    A rootless daemon reads `~/.config/docker/daemon.json` and never opens the one
    in /etc, so this is a question about the running daemon rather than a constant.
    """
    return detect.inspect().path


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def _select(prompt: str, choices: list, current: object) -> str:
    default = next((c for c in choices if getattr(c, "value", None) == current), None)
    return ui.select(prompt, choices, default=default) or str(current)


def _ask_preset(cfg: DockerConfig, found: detect.Docker) -> None:
    choices = []
    for preset in PRESETS.values():
        if preset.key == "current" and not found.existing:
            continue
        choices.append(questionary.Choice(
            title=preset.label, value=preset.key, description=preset.description
        ))
    chosen = _select("Start from:", choices, cfg.preset)
    detect.apply_preset(cfg, chosen, found)


def _ask_logging(cfg: DockerConfig, found: detect.Docker) -> None:
    """The headline question: where container output goes and how much of it is kept.

    Drivers this daemon cannot load are shown as such rather than hidden — a host
    with a plugin the wizard has never heard of should not be told its driver is
    wrong, and a host missing one should not be allowed to pick it silently.
    """
    choices = []
    for driver in LOG_DRIVERS.values():
        missing = (
            bool(found.log_plugins)
            and driver.key not in found.log_plugins
            # `none` is built into the daemon and never appears in the plugin list.
            and driver.key not in BUILTIN_DRIVERS
        )
        title = driver.label + ("  (not available on this daemon)" if missing else "")
        description = driver.description + (f"  {driver.note}" if driver.note else "")
        choices.append(questionary.Choice(title=title, value=driver.key, description=description))
    cfg.log_driver = _select("Log driver:", choices, cfg.log_driver)

    spec = cfg.driver()
    if cfg.log_driver in NEEDS_ENDPOINT:
        ui.warn(f"{cfg.log_driver} needs an endpoint configured, or no container will start.")
        ui.dim("  Set its log-opts by hand after saving.")
    if not spec.rotates:
        ui.dim(f"  {spec.note}")
        return

    if cfg.log_driver == "none":
        return

    ui.console.print()
    ui.dim("  A cap is per container. Blank means unbounded, which is the Docker default")
    ui.dim("  and the reason a single chatty container can take a host down.")
    while True:
        size = ui.text_input(
            "Maximum size per log file (e.g. 10m, 512k, 2g):",
            default=cfg.log_max_size or DEFAULT_MAX_SIZE,
        )
        if not size or valid_size(size):
            cfg.log_max_size = size
            break
        ui.error("Not a size Docker will accept — a number with an optional k, m or g.")

    if cfg.log_max_size:
        while True:
            count = ui.text_input(
                "How many rotated files to keep:", default=cfg.log_max_file or DEFAULT_MAX_FILE
            )
            if count.isdigit() and int(count) >= 1:
                cfg.log_max_file = count
                break
            ui.error("A whole number, 1 or more.")
        # Measured: compress with max-file below 2 makes every container fail to
        # start, and `dockerd --validate` accepts it happily. So it is only offered
        # when it can work.
        if int(cfg.log_max_file) >= 2 and "compress" in spec.opts:
            cfg.log_compress = ui.confirm(
                "Compress rotated log files?", default=cfg.log_compress
            )
        else:
            cfg.log_compress = False
    else:
        cfg.log_max_file = ""
        cfg.log_compress = False


def _ask_toggles(cfg: DockerConfig) -> None:
    choices = [
        questionary.Choice(
            title=SETTINGS[key].label,
            value=key,
            checked=bool(getattr(cfg, key)),
            description=SETTINGS[key].why or SETTINGS[key].description,
        )
        for key in TOGGLE_SETTINGS
    ]
    selected = set(ui.checkbox(
        "Daemon behaviour:", choices=choices, instruction="(Space toggle · Enter confirm)"
    ))
    for key in TOGGLE_SETTINGS:
        setattr(cfg, key, key in selected)


def _ask_networking(cfg: DockerConfig) -> None:
    ui.console.print()
    ui.dim("  Docker carves user networks out of 172.17.0.0/16 by default, which collides")
    ui.dim("  with a lot of corporate VPN ranges. Blank leaves Docker's default alone.")
    current = " ".join(f"{base}:{size}" for base, size in cfg.address_pools)
    answer = ui.text_input("Address pools (base:size, space separated):", default=current)
    pools: list[tuple[str, int]] = []
    for token in answer.split():
        base, _, size = token.partition(":")
        if not base:
            continue
        try:
            pools.append((base, int(size) if size else 24))
        except ValueError:
            ui.warn(f"Ignoring '{token}' — expected base:size, e.g. 10.201.0.0/16:24")
    cfg.address_pools = pools

    cfg.dns = ui.text_input(
        "DNS servers for containers (space separated, blank for the host's):",
        default=" ".join(cfg.dns),
    ).split()


def _ask_registries(cfg: DockerConfig) -> None:
    cfg.registry_mirrors = ui.text_input(
        "Registry mirrors (space separated, blank for none):",
        default=" ".join(cfg.registry_mirrors),
    ).split()
    cfg.insecure_registries = ui.text_input(
        "Insecure registries (space separated, blank for none):",
        default=" ".join(cfg.insecure_registries),
    ).split()
    if cfg.insecure_registries:
        ui.warn("Certificate verification is disabled for those hosts.")


def _ask_advanced(cfg: DockerConfig) -> None:
    cfg.data_root = ui.text_input(
        "Where images and containers live:", default=cfg.data_root
    ) or SETTINGS["data_root"].default  # type: ignore[assignment]
    if cfg.data_root != SETTINGS["data_root"].default:
        ui.warn("Changing this does not move what is already stored under the old path.")
    cfg.metrics_addr = ui.text_input(
        "Prometheus metrics address (blank for none):", default=cfg.metrics_addr
    )
    while True:
        answer = ui.text_input(
            "Parallel image layer downloads:", default=str(cfg.max_concurrent_downloads)
        )
        if answer.isdigit() and int(answer) >= 1:
            cfg.max_concurrent_downloads = int(answer)
            break
        ui.error("A whole number, 1 or more.")


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _show_preview(cfg: DockerConfig, found: detect.Docker) -> None:
    ui.console.print()
    ui.console.print("  [dim]summary[/]")
    ui.console.print()
    for label, value in render.summary(cfg):
        ui.console.print(f"  [bold cyan]{label:<18}[/] {value}")

    rows = render.setting_rows(cfg)
    ui.console.print()
    if not rows:
        ui.dim("  Nothing is set — this writes an empty config and every Docker default applies.")
        ui.console.print()
        return

    ui.console.print("  [dim]what this changes from Docker's defaults[/]")
    ui.console.print()
    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("  area")
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_column("note")
    for group, key, value, why in rows:
        table.add_row(f"  [dim]{group}[/]", key, value, f"[dim]{why}[/]")
    ui.console.print(table)

    if not cfg.logs_are_capped():
        ui.console.print()
        ui.warn("Container logs are still uncapped.")

    for warning in cfg.warnings():
        ui.console.print()
        ui.warn(warning)

    if found.daemon and not found.in_group and found.group_exists:
        ui.console.print()
        ui.dim("  You are not in the docker group, so `docker` needs sudo here.")
    ui.console.print()


def _run_check(
    cfg: DockerConfig, found: detect.Docker, *, quiet_when_ok: bool = False
) -> bool:
    with ui.spinner("Checking the configuration…"):
        report = validate.verify(cfg, found)

    if report.ok and quiet_when_ok:
        ui.dim(f"  {len(report.checks)} checks pass.")
        return True

    ui.console.print()
    if validate.available():
        ui.console.print(f"  [dim]checked with[/] [bold]dockerd {report.version}[/]")
    else:
        ui.console.print("  [dim]checked without dockerd — shape and model checks only[/]")
    ui.console.print()
    for check in report.checks:
        mark = "[green]✔[/]" if check.ok else "[red]✖[/]"
        ui.console.print(f"  {mark} {check.name}  [dim]{check.detail}[/]")
    ui.console.print()
    return report.ok


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def _confirm_overwrite(cfg: DockerConfig, found: detect.Docker, path: Path) -> bool:
    """Show what changes, and ask.

    JSON has no comment syntax, so unlike the pre-commit config this file cannot
    carry a "generated by devstuff" marker — there is no way to tell the wizard's
    own output from a hand-written file. A diff is a better answer than a marker
    anyway: it says exactly what is at stake rather than who wrote it.
    """
    if not path.exists():
        return True

    # Read the file being replaced, which is not necessarily the daemon's own —
    # `--output` writes somewhere else entirely, and diffing against /etc/docker
    # would describe changes to a file nothing is about to touch.
    try:
        current = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        ui.console.print()
        ui.warn(f"{path} exists but is not readable by you, so it cannot be shown.")
        ui.dim("  A timestamped backup is taken before it is replaced.")
        return ui.confirm("Replace it anyway?", default=False)

    lines = render.diff(current, render.to_json(cfg))
    ui.console.print()
    if not lines:
        ui.success(f"{path} already contains exactly this. Nothing to write.")
        return False
    ui.console.print(f"  [dim]changes to[/] [bold]{path}[/]")
    ui.code_block("\n".join(lines), language="diff")
    if found.invalid:
        ui.warn("The existing file is not valid JSON — the daemon is ignoring it right now.")
    ui.dim("  A timestamped backup is taken first.")
    return ui.confirm("Apply these changes?", default=True)


def _offer_restart(cfg: DockerConfig, found: detect.Docker) -> None:
    """The daemon does not read the file until it restarts.

    live-restore only helps if it was on *before* this restart, so what matters is
    the daemon's current state rather than what was just written — which is why this
    reads `found.live_restore` and not `cfg.live_restore`.
    """
    ui.console.print()
    if not found.systemd:
        ui.dim("  Nothing was restarted. The daemon reads this file the next time it starts.")
        return

    if found.containers and not found.live_restore:
        ui.warn(
            f"{found.containers} container{'s' if found.containers != 1 else ''} "
            "are running and live-restore is currently off, so a restart stops them."
        )
        if cfg.live_restore:
            ui.dim("  live-restore is on in the new config — it protects the *next* restart.")
    elif found.live_restore:
        ui.dim("  live-restore is already on, so running containers survive the restart.")

    if not ui.confirm("Restart the Docker daemon now?", default=not found.containers):
        ui.dim("  Apply it later with:  sudo systemctl restart docker")
        return

    with ui.spinner("Restarting docker…"):
        ok, message = validate.restart()
    if not ok:
        ui.error(f"Could not restart docker: {message}")
        ui.dim("  Check what it made of the config:  sudo journalctl -u docker -n 40")
        return
    ui.success("Docker restarted.")
    _confirm_effective(cfg)


def _confirm_effective(cfg: DockerConfig) -> None:
    """Read back what the daemon believes, so the save is proved rather than assumed."""
    info = validate.effective()
    if info is None:
        ui.warn("The daemon did not answer after the restart.")
        ui.dim("  sudo journalctl -u docker -n 40")
        return
    driver = str(info.get("LoggingDriver") or "")
    if driver == cfg.log_driver:
        ui.success(f"The daemon is now using the {driver} log driver.")
    else:
        ui.warn(f"The daemon reports the {driver} driver, not {cfg.log_driver}.")
        ui.dim("  Something else may be setting it — check for a drop-in unit or DOCKER_OPTS.")


def _offer_group_membership(found: detect.Docker) -> None:
    """`usermod -aG docker` — orthogonal to daemon.json, and the other half of a
    working Docker install. Offered once, never forced."""
    if found.in_group or found.rootless or not found.group_exists:
        return
    ui.console.print()
    ui.dim("  You are not in the docker group, so every `docker` command needs sudo.")
    if not ui.confirm("Add your user to the docker group?", default=True):
        return
    ok, message = validate.add_to_group()
    if not ok:
        ui.error(f"Could not add you to the docker group: {message}")
        return
    ui.success(f"Added {message} to the docker group.")
    ui.warn("Group membership only applies to new logins — log out and back in.")
    ui.dim("  Anyone in this group can run containers as root on the host. That is by design.")


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

_MENU = {
    "save": "Save this configuration",
    "preset": "Start from a different preset",
    "logging": "Change the log driver and rotation",
    "toggles": "Change daemon behaviour",
    "network": "Change address pools and DNS",
    "registry": "Change registry mirrors",
    "advanced": "Change storage, metrics and download concurrency",
    "check": "Check this against dockerd",
    "json": "Show the generated daemon.json",
    "cancel": "Cancel without saving",
}


def _report(found: detect.Docker) -> None:
    if not found.cli:
        ui.warn("Docker is not installed — the wizard can still write a config for later.")
        ui.dim("  Install it with:  devstuff install docker")
    elif not found.daemon:
        ui.warn("The Docker daemon is not answering, so its current state is unknown.")
        ui.dim("  sudo systemctl start docker")
    else:
        ui.dim(
            f"Docker {found.version} · storage {found.storage_driver} · "
            f"logs {found.log_driver} · {found.containers} containers"
        )
        if found.rootless:
            ui.dim("Rootless daemon — its config lives in your home, not /etc.")
        if found.live_restore:
            ui.dim("live-restore is on: running containers survive a daemon restart.")

    if found.existing:
        ui.dim(f"Existing config: {found.path} ({len(found.existing)} keys)")
    elif found.unreadable:
        ui.dim(f"Existing config: {found.path} (not readable without sudo)")
    elif found.invalid:
        ui.warn(f"{found.path} is not valid JSON — the daemon cannot be reading it.")
    else:
        ui.dim(f"No {found.path} yet.")

    if found.daemon and found.log_driver == "json-file":
        # The whole reason this configurator exists.
        ui.warn("Container logs are uncapped by default and grow until the disk is full.")
    if found.group_exists and not found.in_group and not found.rootless:
        ui.dim("You are not in the docker group — `docker` needs sudo here.")


def run(*, target: Path | None = None) -> DockerConfig | None:
    """Walk the wizard. Returns the config, or None if the user cancelled.

    `target` writes somewhere else entirely and suppresses everything that touches
    the running daemon — no sudo, no restart, no group change.
    """
    found = detect.inspect()
    cfg = detect.suggest(found)
    if target is not None:
        cfg.target = target

    ui.section("Configure the Docker daemon")
    ui.dim("Pick a starting point, adjust it, and see what changes from Docker's own")
    ui.dim("defaults before anything is written. Ctrl-C to bail out.")
    ui.console.print()
    _report(found)

    ui.console.print()
    _ask_preset(cfg, found)
    _ask_logging(cfg, found)

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
        if action == "json":
            ui.code_block(render.to_json(cfg), language="json")
            continue
        if action == "check":
            _run_check(cfg, found)
            continue
        if action == "preset":
            _ask_preset(cfg, found)
            _ask_logging(cfg, found)
        elif action == "logging":
            _ask_logging(cfg, found)
        elif action == "toggles":
            _ask_toggles(cfg)
        elif action == "network":
            _ask_networking(cfg)
        elif action == "registry":
            _ask_registries(cfg)
        elif action == "advanced":
            _ask_advanced(cfg)

    path = target or cfg.target

    if not _run_check(cfg, found, quiet_when_ok=True) and not ui.confirm(
        "Some checks failed. Save anyway?", default=False
    ):
        ui.dim("Nothing was written.")
        return None

    if not _confirm_overwrite(cfg, found, path):
        ui.dim("Nothing was written.")
        return None

    needs_sudo = target is None and not found.writable()
    if needs_sudo:
        ui.console.print()
        ui.dim(f"  {path} is root-owned, so this runs:")
        ui.dim(f"    sudo install -m 0644 -o root -g root <tmp> {path}")

    # Always, including on the --output path: the overwrite prompt promises a
    # backup, and a promise that only holds for some paths is worse than none.
    saved_backup, message = validate.backup(path, sudo=needs_sudo)
    if saved_backup is None and path.exists():
        ui.error(f"Could not back up the existing config: {message}")
        if not ui.confirm("Continue without a backup?", default=False):
            return None

    ok, message = validate.write(cfg, path, sudo=needs_sudo)
    if not ok:
        ui.error(f"Could not write {path}: {message}")
        return None

    ui.console.print()
    ui.success(f"Saved {path}")
    if saved_backup is not None:
        ui.dim(f"  Previous version backed up to {saved_backup.name}")

    if target is not None:
        ui.console.print()
        ui.dim(f"Try it without installing it:  dockerd --validate --config-file {path}")
    else:
        _offer_restart(cfg, found)
        _offer_group_membership(found)

    ui.console.print()
    ui.dim(f"Re-run any time:  devstuff configure docker   ·   edit: {path}")
    ui.console.print()
    return cfg


__all__ = ["config_path", "run"]
