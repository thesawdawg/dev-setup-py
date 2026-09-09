"""Configure Linux Yama permissions without inventing a reptyr rc file.

References: upstream reptyr.1 and docs.kernel.org/admin-guide/LSM/Yama.html.
Only sysctl modes 0-2 are offered: mode 3 cannot be changed until reboot.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import questionary

from dev_setup import ui
from dev_setup.generic import _run

SCOPE_PATH = Path("/proc/sys/kernel/yama/ptrace_scope")
SYSTEM_PATH = Path("/etc/sysctl.d/99-devstuff-reptyr.conf")
SETTING = "kernel.yama.ptrace_scope"
MODES = {
    0: "Enable same-user attach (0) — permits ordinary reptyr PID",
    1: "Restrict attach (1) — descendants or explicitly permitted processes",
    2: "Admin-only attach (2) — requires CAP_SYS_PTRACE",
}


def config_path() -> Path:
    return SYSTEM_PATH


def read_scope() -> int | None:
    try:
        value = int(SCOPE_PATH.read_text().strip())
    except (OSError, ValueError):
        return None
    return value if value in (0, 1, 2, 3) else None


def render(scope: int) -> str:
    if scope not in MODES:
        raise ValueError("Only ptrace_scope values 0, 1 and 2 are supported")
    return (
        "# Managed by devstuff configure reptyr; affects all processes on this host.\n"
        "# 0: same-user attach; 1: restricted attach; 2: admin-only attach.\n"
        f"{SETTING} = {scope}\n"
    )


def _usage() -> None:
    ui.info("reptyr has no rc file or persistent command-line defaults.")
    for line in (
        "reptyr PID      Move a process into this terminal (for example, inside tmux).",
        "reptyr -T PID   Steal the whole TTY without ptracing the target; SSH children need root.",
        "reptyr -s PID   Redirect stdin/out/err even when they were not attached to a terminal.",
        "reptyr -l       Create a PTY; optional COMMAND receives REPTYR_PTY in its environment.",
        "reptyr -L COMMAND   Also attach the command's standard streams and session to the PTY.",
        "reptyr -V PID   Debug logging; -v prints the version; -h shows installed-version help.",
    ):
        ui.dim(line)


def run(*, target: Path | None = None) -> int | None:
    ui.section("reptyr permissions")
    _usage()
    current = read_scope()
    ui.info(f"Current {SETTING}: {current if current is not None else 'unavailable'}")
    # --output is always an offline export, even on a host without Yama.
    if target is None and current is None:
        ui.warn("Yama is absent or unreadable; no permission change can be applied here.")
        ui.dim("Other ptrace restrictions may still apply. Use --output to export a sysctl config.")
        return None
    if target is None and current == 3:
        ui.warn("ptrace_scope=3 is locked until reboot, even for root. No changes made.")
        return None

    choices = [questionary.Choice("Keep current permissions / cancel", value="keep")]
    choices.extend(questionary.Choice(label, value=str(value)) for value, label in MODES.items())
    selected = ui.select("Permission policy:", choices)
    if not selected or selected == "keep":
        return None
    scope = int(selected)
    content = render(scope)
    if scope == 0:
        ui.warn("This relaxes ptrace protection system-wide: same-user processes can inspect or control")
        ui.warn("other dumpable processes. Other users' permissions and container policies still apply.")

    lifetime = "persistent"
    if target is None:
        lifetime = ui.select("Apply for:", [
            questionary.Choice("This boot only (no files changed)", value="temporary"),
            questionary.Choice("Now and at boot (save a sysctl drop-in)", value="persistent"),
            questionary.Choice("Cancel", value="cancel"),
        ])
        if lifetime not in ("temporary", "persistent"):
            return None

    if target is not None:
        ui.info(f"Export only: {target}; existing content will be replaced. No sudo or live changes.")
    elif lifetime == "temporary":
        ui.info(f"Will run: sudo sysctl -w {SETTING}={scope}")
        ui.dim("Existing boot configuration stays in place and may restore a different value.")
    else:
        ui.info(f"Will save {SYSTEM_PATH} with sudo install (numbered backup if it exists).")
        ui.info(f"Then run: sudo sysctl --load {SYSTEM_PATH}")
        ui.dim("Other sysctl files can override this at boot; only this drop-in is loaded now.")
    ui.console.print(content, markup=False)
    if not ui.confirm("Apply this configuration?", default=False):
        return None

    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        ui.success(f"Exported to {target}; live permissions unchanged.")
        return scope

    saved = False
    try:
        if lifetime == "persistent":
            with tempfile.TemporaryDirectory(prefix="devstuff-reptyr-") as directory:
                source = Path(directory) / "reptyr.conf"
                source.write_text(content, encoding="utf-8")
                _run(["sudo", "install", "--backup=numbered", "-m", "0644", str(source), str(SYSTEM_PATH)])
            saved = True
            _run(["sudo", "sysctl", "--load", str(SYSTEM_PATH)])
        else:
            _run(["sudo", "sysctl", "-w", f"{SETTING}={scope}"])
    except (RuntimeError, OSError) as exc:
        ui.error(f"Could not apply reptyr permissions: {exc}")
        if saved:
            ui.warn(f"{SYSTEM_PATH} was saved, but the live change failed; review it before reboot.")
        return None
    if read_scope() != scope:
        ui.error("The live ptrace_scope does not match the requested value; permissions are not verified.")
        return None
    ui.success(f"Verified {SETTING}={scope}.")
    return scope
