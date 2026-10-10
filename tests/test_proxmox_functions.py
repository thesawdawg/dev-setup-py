"""End-to-end tests for the pve-* functions, against a Proxmox made of stubs.

There is no PVE host to test against, so `tests/proxmox_stub/` puts `ssh` and `curl`
scripts on PATH that answer the same API paths a real node would and record what they
were asked. Every function then runs for real: the same bash, the same `lib.sh`, the same
`devstuff configure proxmox --export` bridge.

What this proves is what devstuff sends, and what it does with the answer — resolution,
the qm/pct choice, the confirmation gate, the exit codes. What Proxmox does with the
command is beyond it; `devstuff run pve-check` is what closes that gap on a real host.
"""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from dev_setup import function_runner, functions_registry

STUB_DIR = Path(__file__).parent / "proxmox_stub"
REPO_SRC = Path(__file__).resolve().parents[1] / "src"

CONFIG = """\
version: 1
default: lab
profiles:
  lab:
    transport: ssh
    host: pve1.example
    node: pve1
    user: root
    auth: agent
  cloud:
    transport: api
    host: pve1.example
    node: pve1
    token_id: root@pam!devstuff
    secret_env: PVE_TEST_TOKEN
  viasudo:
    transport: ssh
    host: pve1.example
    node: pve1
    user: ops
    auth: agent
    sudo: true
"""


class Harness:
    """A temp HOME with profiles, a PATH with the stubs, and a log of what they saw."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.bin = root / "bin"
        self.log = root / "calls.log"
        (self.home / ".config" / "devstuff").mkdir(parents=True)
        self.config = self.home / ".config" / "devstuff" / "proxmox.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.bin.mkdir()
        for name in ("ssh", "curl", "router.py"):
            shutil.copy2(STUB_DIR / name, self.bin / name)
            (self.bin / name).chmod(0o755)
        shim = self.bin / "devstuff"
        shim.write_text(
            f"#!/bin/sh\nexec {sys.executable!r} -m dev_setup \"$@\"\n".replace("'", '"'),
            encoding="utf-8",
        )
        shim.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "HOME": str(self.home),
            "DEVSTUFF_BIN": str(self.bin / "devstuff"),
            "PVE_STUB_LOG": str(self.log),
            "PYTHONPATH": str(REPO_SRC),
            "PVE_TEST_TOKEN": "s3cret-token-value",
            "TERM": "dumb",
        }
        env.update(extra)
        return env

    def script_for(self, key: str, args: tuple[str, ...]) -> tuple[Path, list[str]]:
        fn = functions_registry.get(key)
        assert fn is not None, key
        values = function_runner.resolve_params(fn.params, args)
        prelude = function_runner._positional_prelude(fn.params)
        body = f"{prelude}\n{fn.script}" if prelude else fn.script
        path = self.root / f"{key}.sh"
        path.write_text(body, encoding="utf-8")
        return path, values

    def run(self, key: str, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        path, values = self.script_for(key, args)
        return subprocess.run(
            ["bash", str(path), *values],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=self.env(**extra),
            timeout=120,
        )

    def run_tty(self, key: str, *args: str, answer: str = "y\n", **extra: str) -> tuple[int, str]:
        """Same, but with a real terminal on stdin and stdout.

        pve_confirm requires both to be a TTY (SD-7), so the accept and decline paths
        cannot be reached any other way.
        """
        path, values = self.script_for(key, args)
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            ["bash", str(path), *values],
            stdin=slave,
            stdout=slave,
            stderr=subprocess.STDOUT,
            env=self.env(**extra),
            close_fds=True,
        )
        os.close(slave)
        os.write(master, answer.encode())
        output = b""
        try:
            proc.wait(timeout=120)
        finally:
            os.set_blocking(master, False)
            try:
                while chunk := os.read(master, 65536):
                    output += chunk
            except (BlockingIOError, OSError):
                pass
            os.close(master)
        return proc.returncode, output.decode(errors="replace")

    def calls(self, kind: str = "SSH") -> list[str]:
        if not self.log.exists():
            return []
        return [
            line.split("\t", 1)[1]
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.startswith(f"{kind}\t")
        ]

    def commands(self) -> list[str]:
        """What the node actually ran, after any cluster hop and sudo prefix."""
        return [c for c in self.calls("RUN") if c.split(" ")[0] in ("qm", "pct", "vzdump")]


@pytest.fixture
def pve(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# ---------------------------------------------------------------------------
# The shell itself
# ---------------------------------------------------------------------------


def test_lib_and_every_script_parse() -> None:
    """`bash -n` over the helper and all fourteen bodies."""
    from dev_setup.configure.proxmox import render

    checked = [render.LIB_PATH]
    assert subprocess.run(["bash", "-n", str(render.LIB_PATH)]).returncode == 0

    for fn in functions_registry.all_functions():
        if fn.category != "proxmox":
            continue
        prelude = function_runner._positional_prelude(fn.params)
        body = f"{prelude}\n{fn.script}"
        result = subprocess.run(["bash", "-n", "-c", body], capture_output=True, text=True)
        assert result.returncode == 0, f"{fn.key}: {result.stderr}"
        checked.append(fn.key)
    assert len(checked) == 15


def test_every_function_bootstraps_from_the_package() -> None:
    """Each one sources lib.sh through the export bridge rather than reimplementing it."""
    for fn in functions_registry.all_functions():
        if fn.category != "proxmox":
            continue
        assert "configure proxmox --export" in fn.script, fn.key
        assert '. "$PVE_LIB"' in fn.script, fn.key
        # `eval "$(cmd)"` would swallow the failure: eval reports on the text it ran, not
        # on the command that produced it, so the two-step assignment is load-bearing.
        assert 'eval "$(' not in fn.script, fn.key


# ---------------------------------------------------------------------------
# Reads, over both transports
# ---------------------------------------------------------------------------


def test_guests_over_ssh(pve: Harness) -> None:
    result = pve.run("pve-guests")
    assert result.returncode == 0, result.stderr
    assert "web01" in result.stdout
    assert "dns" in result.stdout
    assert "VM" in result.stdout and "CT" in result.stdout
    assert pve.calls("SSH") == [
        "pvesh get /cluster/resources --type vm --output-format json"
    ]


def test_guests_over_api(pve: Harness) -> None:
    result = pve.run("pve-guests", "", "cloud")
    assert result.returncode == 0, result.stderr
    assert "web01" in result.stdout
    assert pve.calls("SSH") == []
    assert "/cluster/resources" in pve.calls("CURL")[0]
    assert "('type', 'vm')" in pve.calls("CURL")[0]


def test_api_token_never_reaches_argv(pve: Harness) -> None:
    """NFR-3: the credential goes in on stdin, where ps cannot publish it."""
    pve.run("pve-guests", "", "cloud")
    assert pve.calls("CRED") == ["stdin=True argv=False"]


@pytest.mark.parametrize(
    "token", ["plain-uuid-4d2", 'has"a-quote', "has\\a-backslash", "has spaces in it"]
)
def test_an_awkward_token_reaches_proxmox_intact(pve: Harness, token: str) -> None:
    """curl's config format is quoted with backslash escapes, so a secret containing a
    quote would end the header early and send a truncated credential."""
    result = pve.run("pve-guests", "", "cloud", PVE_TEST_TOKEN=token)
    assert result.returncode == 0, result.stderr
    assert pve.calls("HEADER") == [f"Authorization: PVEAPIToken=root@pam!devstuff={token}"]


def test_guest_filter(pve: Harness) -> None:
    result = pve.run("pve-guests", "dns")
    assert "dns" in result.stdout
    assert "web01" not in result.stdout


def test_guest_filter_with_no_match_is_success(pve: Harness) -> None:
    result = pve.run("pve-guests", "nothing-like-this")
    assert result.returncode == 0
    assert "No guest matches" in result.stdout


def test_status_reports_cluster_and_nodes(pve: Harness) -> None:
    result = pve.run("pve-status")
    assert result.returncode == 0, result.stderr
    assert "Proxmox VE 8.2.4" in result.stdout
    assert "quorate: yes" in result.stdout
    assert "pve1" in result.stdout and "pve2" in result.stdout


def test_storage_covers_every_node(pve: Harness) -> None:
    result = pve.run("pve-storage")
    assert result.returncode == 0, result.stderr
    assert "local-lvm" in result.stdout
    assert "nearly full" in result.stdout  # local-lvm is at 90%
    assert result.stdout.count("nas") >= 2  # shared, so it is on both nodes


def test_storage_for_one_node(pve: Harness) -> None:
    result = pve.run("pve-storage", "pve2")
    assert "local-lvm" not in result.stdout
    assert [c for c in pve.calls("SSH") if "/nodes/pve1/storage" in c] == []


def test_snapshots_are_listed_without_the_current_pseudo_entry(pve: Harness) -> None:
    result = pve.run("pve-snapshots", "web01")
    assert result.returncode == 0, result.stderr
    assert "pre-upgrade" in result.stdout
    assert "nightly" in result.stdout
    # Proxmox reports a pseudo-snapshot called "current" for the live state.
    assert "You are here" not in result.stdout


def test_no_snapshots_is_a_successful_answer(pve: Harness) -> None:
    """FR-21: finding nothing exits 0, or a correct answer wears a failure banner."""
    result = pve.run("pve-snapshots", "dns")
    assert result.returncode == 0
    assert "no snapshots" in result.stdout.lower()


def test_no_updates_is_a_successful_answer(pve: Harness) -> None:
    result = pve.run("pve-updates", "pve1")
    assert result.returncode == 0
    assert "No updates are pending" in result.stdout


def test_updates_lists_packages_and_the_subscription_state(pve: Harness) -> None:
    result = pve.run("pve-updates", "pve2")
    assert result.returncode == 0
    assert "pve-manager" in result.stdout
    assert "Subscription: notfound" in result.stdout


def test_updates_over_api_explains_the_sys_modify_403(pve: Harness) -> None:
    """The measured surprise: listing pending updates is guarded by Sys.Modify."""
    result = pve.run("pve-updates", "pve1", "cloud", PVE_STUB_FORBID="/nodes/pve1/apt")
    assert result.returncode != 0
    assert "403" in result.stderr
    assert "Sys.Modify" in result.stderr
    assert "PVEAuditor is not enough" in result.stderr


def test_backups_deduplicate_shared_storage(pve: Harness) -> None:
    result = pve.run("pve-backups")
    assert result.returncode == 0, result.stderr
    # `nas` is shared by both nodes with identical contents; listing it twice would
    # double every archive on it.
    assert result.stdout.count("nas (via") == 1
    assert "vzdump-qemu-101" in result.stdout


def test_backups_for_one_guest(pve: Harness) -> None:
    result = pve.run("pve-backups", "dns")
    assert result.returncode == 0, result.stderr
    assert "vzdump-lxc-200" in result.stdout
    assert "vzdump-qemu-101" not in result.stdout


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolution_by_name_picks_the_right_binary(pve: Harness) -> None:
    pve.run("pve-start", "mail", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["pct start 201"]


def test_resolution_by_vmid(pve: Harness) -> None:
    pve.run("pve-start", "102", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["qm start 102"]


def test_unknown_guest_exits_two_and_suggests(pve: Harness) -> None:
    result = pve.run("pve-start", "web02")
    assert result.returncode == 2
    assert "No guest called" in result.stderr
    assert "did you mean web01" in result.stderr
    assert pve.commands() == []


def test_ambiguous_name_lists_candidates_and_runs_nothing(pve: Harness) -> None:
    """SD-9: two guests share the name, so neither is chosen."""
    result = pve.run("pve-stop", "twin", "stop", DEVSTUFF_PVE_ASSUME_YES="1")
    assert result.returncode == 2
    assert "matches 2 guests" in result.stderr
    assert "300" in result.stderr and "301" in result.stderr
    assert pve.commands() == []


def test_a_locked_guest_is_flagged(pve: Harness) -> None:
    result = pve.run("pve-start", "locked-vm", DEVSTUFF_PVE_ASSUME_YES="1")
    assert "locked (backup)" in result.stderr or "locked" in result.stderr


# ---------------------------------------------------------------------------
# Writes: the gate
# ---------------------------------------------------------------------------


def test_write_is_refused_on_an_api_profile(pve: Harness) -> None:
    result = pve.run("pve-start", "web01", "cloud")
    assert result.returncode != 0
    assert "is an API profile" in result.stderr
    assert "devstuff configure proxmox" in result.stderr
    assert pve.commands() == []


def test_write_without_a_terminal_refuses_rather_than_hanging(pve: Harness) -> None:
    result = pve.run("pve-start", "db01")
    assert result.returncode != 0
    assert "no terminal here to confirm on" in result.stderr
    assert "DEVSTUFF_PVE_ASSUME_YES" in result.stderr
    assert pve.commands() == []


def test_the_command_is_printed_before_it_is_confirmed(pve: Harness) -> None:
    """FR-19: whatever happens next, the user has been shown the real command."""
    result = pve.run("pve-start", "db01")
    assert "→ ssh pve2 qm\\ start\\ 102" in result.stderr or "qm start 102" in result.stderr
    assert result.returncode != 0


def test_assume_yes_runs_exactly_what_it_printed(pve: Harness) -> None:
    result = pve.run("pve-start", "build01", DEVSTUFF_PVE_ASSUME_YES="1")
    assert result.returncode == 0, result.stderr
    assert pve.commands() == ["qm start 103"]


def test_declining_at_the_prompt_is_not_a_failure(pve: Harness) -> None:
    code, output = pve.run_tty("pve-start", "db01", answer="n\n")
    assert code == 0, output
    assert "Nothing was run" in output
    assert pve.commands() == []


def test_accepting_at_the_prompt_runs_it(pve: Harness) -> None:
    code, output = pve.run_tty("pve-start", "build01", answer="y\n")
    assert code == 0, output
    assert pve.commands() == ["qm start 103"]


# ---------------------------------------------------------------------------
# Writes: the commands themselves
# ---------------------------------------------------------------------------


def test_a_guest_on_another_node_is_reached_through_a_hop(pve: Harness) -> None:
    """qm is node-local, so a guest on pve2 cannot be started by running qm on pve1."""
    pve.run("pve-start", "db01", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.calls("SSH") == [
        "pvesh get /cluster/resources --type vm --output-format json",
        "ssh pve2 qm\\ start\\ 102",
    ]


def test_a_guest_on_the_connected_node_is_not(pve: Harness) -> None:
    pve.run("pve-start", "build01", DEVSTUFF_PVE_ASSUME_YES="1")
    assert "ssh pve1" not in " ".join(pve.calls("SSH"))
    assert pve.commands() == ["qm start 103"]


def test_sudo_profile_prefixes_the_command(pve: Harness) -> None:
    """qm, pct and vzdump need root, so a non-root user goes through sudo -n."""
    pve.run("pve-start", "build01", "viasudo", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["qm start 103"]
    assert any(c.startswith("sudo -n qm") for c in pve.calls("SSH"))


def test_already_running_is_a_successful_no_op(pve: Harness) -> None:
    result = pve.run("pve-start", "web01")
    assert result.returncode == 0
    assert "already running" in result.stdout
    assert pve.commands() == []


def test_already_stopped_is_a_successful_no_op(pve: Harness) -> None:
    result = pve.run("pve-stop", "db01")
    assert result.returncode == 0
    assert "already stopped" in result.stdout


def test_shutdown_is_graceful_and_carries_a_timeout(pve: Harness) -> None:
    pve.run("pve-stop", "web01", "shutdown", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["qm shutdown 101 --timeout 60"]


def test_stop_is_the_hard_one_and_says_so(pve: Harness) -> None:
    result = pve.run("pve-stop", "web01", "stop", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["qm stop 101"]
    assert "power cut" in result.stderr


def test_an_unknown_stop_mode_is_rejected_before_anything_runs(pve: Harness) -> None:
    result = pve.run("pve-stop", "web01", "destroy")
    assert result.returncode == 2
    assert "mode must be" in result.stderr
    assert pve.calls("SSH") == []


def test_restart_of_a_stopped_guest_points_at_start(pve: Harness) -> None:
    result = pve.run("pve-restart", "db01")
    assert result.returncode == 0
    assert "devstuff run pve-start db01" in result.stdout
    assert pve.commands() == []


def test_a_running_vm_migrates_live(pve: Harness) -> None:
    pve.run("pve-migrate", "web01", "pve2", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["qm migrate 101 pve2 --online 1"]


def test_a_running_container_migrates_with_restart(pve: Harness) -> None:
    """The measured asymmetry: pct has no --online, because it cannot live-migrate."""
    result = pve.run("pve-migrate", "dns", "pve1", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["pct migrate 200 pve1 --restart 1"]
    assert "cannot live-migrate" in result.stderr


def test_a_stopped_guest_migrates_with_neither_flag(pve: Harness) -> None:
    pve.run("pve-migrate", "db01", "pve1", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == ["qm migrate 102 pve1"]


def test_migrating_to_an_unknown_node_lists_the_real_ones(pve: Harness) -> None:
    result = pve.run("pve-migrate", "web01", "pve9")
    assert result.returncode == 2
    assert "No node called 'pve9'" in result.stderr
    assert "pve1" in result.stderr and "pve2" in result.stderr
    assert pve.commands() == []


def test_migrating_to_the_node_it_is_already_on_is_a_no_op(pve: Harness) -> None:
    result = pve.run("pve-migrate", "web01", "pve1")
    assert result.returncode == 0
    assert "already on pve1" in result.stdout
    assert pve.commands() == []


def test_snapshot_passes_the_description_as_one_argument(pve: Harness) -> None:
    result = pve.run(
        "pve-snapshot", "web01", "pre-patch", "before the 8.3 upgrade",
        DEVSTUFF_PVE_ASSUME_YES="1",
    )
    assert result.returncode == 0, result.stderr
    assert pve.commands() == [
        "qm snapshot 101 pre-patch --description before the 8.3 upgrade"
    ]


def test_snapshot_mentions_vmstate_only_for_a_running_vm(pve: Harness) -> None:
    running_vm = pve.run("pve-snapshot", "web01", "snap1", "", DEVSTUFF_PVE_ASSUME_YES="1")
    assert "--vmstate 1" in running_vm.stderr
    container = pve.run("pve-snapshot", "dns", "snap1", "", DEVSTUFF_PVE_ASSUME_YES="1")
    assert "--vmstate" not in container.stderr


@pytest.mark.parametrize("name", ["9lives", "with space", "semi;colon", "-leading"])
def test_bad_snapshot_names_are_rejected_before_anything_runs(pve: Harness, name: str) -> None:
    result = pve.run("pve-snapshot", "web01", name, "")
    assert result.returncode == 2
    assert pve.calls("SSH") == []


def test_rollback_checks_the_snapshot_exists_first(pve: Harness) -> None:
    result = pve.run("pve-rollback", "web01", "pre-upgrad")
    assert result.returncode == 2
    assert "has no snapshot called" in result.stderr
    assert "pre-upgrade" in result.stderr
    assert pve.commands() == []


def test_rollback_warns_about_what_it_discards(pve: Harness) -> None:
    result = pve.run("pve-rollback", "web01", "pre-upgrade", DEVSTUFF_PVE_ASSUME_YES="1")
    assert result.returncode == 0, result.stderr
    assert "discards everything written since" in result.stderr
    assert pve.commands() == ["qm rollback 101 pre-upgrade"]


def test_backup_uses_the_only_backup_storage_when_there_is_one(pve: Harness) -> None:
    """pve2 has exactly one storage that can hold backups, so it needs no asking."""
    result = pve.run("pve-backup", "dns", "", "", DEVSTUFF_PVE_ASSUME_YES="1")
    assert result.returncode == 0, result.stderr
    assert "Using the only backup storage on pve2: nas" in result.stderr
    assert pve.commands() == ["vzdump 200 --mode snapshot --storage nas --compress zstd"]


def test_backup_asks_which_storage_when_several_could_hold_it(pve: Harness) -> None:
    result = pve.run("pve-backup", "web01", "", "")
    assert result.returncode == 2
    assert "Name a storage" in result.stderr
    assert "local" in result.stderr and "nas" in result.stderr
    assert pve.commands() == []


def test_backup_command_is_vzdump_with_the_chosen_mode(pve: Harness) -> None:
    pve.run("pve-backup", "web01", "nas", "suspend", DEVSTUFF_PVE_ASSUME_YES="1")
    assert pve.commands() == [
        "vzdump 101 --mode suspend --storage nas --compress zstd"
    ]


def test_an_unknown_backup_mode_is_rejected(pve: Harness) -> None:
    result = pve.run("pve-backup", "web01", "nas", "hotcopy")
    assert result.returncode == 2
    assert "mode must be" in result.stderr


# ---------------------------------------------------------------------------
# pve-check, and failure paths
# ---------------------------------------------------------------------------


def test_check_reports_a_working_ssh_profile(pve: Harness) -> None:
    result = pve.run("pve-check")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[ ok ] connect" in result.stdout
    assert "Proxmox VE 8.2.4" in result.stdout
    assert "node tools" in result.stdout
    assert "This profile works" in result.stdout


def test_check_reports_a_working_api_profile(pve: Harness) -> None:
    result = pve.run("pve-check", "cloud")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[ ok ] guest inventory" in result.stdout
    assert "[ ok ] pending updates" in result.stdout


def test_check_names_the_privilege_a_token_is_missing(pve: Harness) -> None:
    result = pve.run("pve-check", "cloud", PVE_STUB_FORBID="/nodes/pve1/apt")
    assert "[warn] pending updates" in result.stdout
    assert "403" in result.stdout


def test_check_fails_when_the_node_is_unreachable(pve: Harness) -> None:
    result = pve.run("pve-check", PVE_STUB_SSH_FAIL="1")
    assert result.returncode == 1
    assert "[FAIL] connect" in result.stdout
    assert "devstuff configure proxmox" in result.stdout


def test_check_reports_a_missing_node_binary(pve: Harness) -> None:
    result = pve.run("pve-check", PVE_STUB_MISSING_TOOLS="vzdump ")
    assert "[FAIL] node tools" in result.stdout
    assert "vzdump" in result.stdout


def test_a_failing_remote_command_is_a_failure(pve: Harness) -> None:
    result = pve.run("pve-start", "build01", DEVSTUFF_PVE_ASSUME_YES="1", PVE_STUB_CMD_FAIL="1")
    assert result.returncode != 0


def test_an_unknown_profile_names_the_ones_that_exist(pve: Harness) -> None:
    result = pve.run("pve-guests", "", "nosuchprofile")
    assert result.returncode != 0
    assert "Unknown Proxmox profile" in result.stderr
    assert "lab" in result.stderr


def test_no_config_at_all_points_at_the_wizard(pve: Harness) -> None:
    pve.config.unlink()
    result = pve.run("pve-guests")
    assert result.returncode != 0
    assert "devstuff configure proxmox" in result.stderr


def test_a_missing_secret_is_reported_before_any_request(pve: Harness) -> None:
    result = pve.run("pve-guests", "", "cloud", PVE_TEST_TOKEN="")
    assert result.returncode != 0
    assert "PVE_TEST_TOKEN" in result.stderr
    assert pve.calls("CURL") == []


def test_a_broken_profile_file_is_a_load_time_error(pve: Harness) -> None:
    pve.config.write_text(
        textwrap.dedent("""\
            version: 1
            default: lab
            profiles:
              lab:
                transport: telepathy
                host: pve1.example
            """),
        encoding="utf-8",
    )
    result = pve.run("pve-guests")
    assert result.returncode != 0
    assert "transport must be one of" in result.stderr
