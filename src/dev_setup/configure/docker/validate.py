"""Check a candidate `daemon.json`, and install it.

`dockerd --validate --config-file <path>` is a real validator and it is used here.
But its coverage stops well short of "this config works", and the gap is the reason
most of this module exists. Measured against Docker 29.6:

| config                                          | `--validate` | what actually happens          |
|-------------------------------------------------|--------------|--------------------------------|
| `"lof-driver"` (a typo)                          | **rejected** | —                              |
| `"base": "notanetwork"`                          | **rejected** | —                              |
| `"max-size": 10` (a number)                      | **rejected** | —                              |
| `"log-driver": "nosuchdriver"`                   | accepted     | every container fails to start |
| `"log-driver": "local", "log-opts": {"bogus":…}` | accepted     | every container fails to start |
| `max-file: "1"` with `compress: "true"`          | accepted     | every container fails to start |
| `"default-address-pools": [{base:/16, size:8}]`  | accepted     | no usable networks             |
| `"hosts"` alongside a systemd unit passing `-H`  | accepted     | daemon refuses to start        |

Those bottom five are the dangerous shape: the daemon starts, reports itself
healthy, and every `docker run` afterwards fails with an error that says nothing
about `daemon.json`. So `verify()` runs `--validate` *and* the checks it does not
do, and treats both as one report.

Every failure path returns a failed `Check`, never an exception. A verification must
never be able to end the wizard, and it never blocks a save.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dev_setup.configure.docker import render
from dev_setup.configure.docker.detect import Docker
from dev_setup.configure.docker.model import BUILTIN_DRIVERS, LOG_DRIVERS, DockerConfig

TIMEOUT = 30
RESTART_TIMEOUT = 120


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Report:
    version: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]


def available() -> bool:
    """Whether `dockerd` is here to validate with.

    Deliberately not `docker`: the CLI can be present with no daemon binary at all
    (a remote context), and it is `dockerd` that reads this file.
    """
    return shutil.which("dockerd") is not None


def _run(cmd: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def version() -> str:
    result = _run(["dockerd", "--version"])
    if result is None or result.returncode != 0:
        return "unknown"
    # "Docker version 29.6.2, build ..."
    parts = result.stdout.strip().split()
    return parts[2].rstrip(",") if len(parts) > 2 else "unknown"


@contextmanager
def sandbox(cfg: DockerConfig):
    """The candidate written to a throwaway file. Yields its path, or None.

    `dockerd --validate` only reads the file — it starts nothing and touches no
    daemon state — so validating never has a side effect on a running Docker.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="devstuff-docker-") as tmp:
            path = Path(tmp) / "daemon.json"
            path.write_text(render.to_json(cfg), encoding="utf-8")
            yield path
    except OSError:  # pragma: no cover
        yield None


def verify(cfg: DockerConfig, found: Docker | None = None) -> Report:
    """Everything checkable without restarting anything. Always returns a report."""
    checks: list[Check] = []

    text = render.to_json(cfg)
    round_trip = render.matches(text, cfg)
    checks.append(Check(
        "generated JSON matches the model",
        round_trip,
        "parsed back identically" if round_trip else "the emitted file says something else",
    ))

    checks += _log_checks(cfg, found)
    checks += _pool_checks(cfg)
    checks += _host_checks(cfg, found)

    if available():
        checks.append(_dockerd_check(cfg))
    else:
        checks.append(Check(
            "dockerd accepts the file",
            True,
            "dockerd is not installed here — skipped, not failed",
        ))
    return Report(version(), checks)


def _dockerd_check(cfg: DockerConfig) -> Check:
    with sandbox(cfg) as path:
        if path is None:  # pragma: no cover
            return Check("dockerd accepts the file", False, "could not write a temporary file")
        result = _run(["dockerd", "--validate", "--config-file", str(path)])
    if result is None:
        return Check("dockerd accepts the file", False, "dockerd did not run")
    output = (result.stdout.strip() or result.stderr.strip()) or "no output"
    if result.returncode == 0:
        return Check("dockerd accepts the file", True, output.splitlines()[-1])
    return Check("dockerd accepts the file", False, output.splitlines()[-1])


def _log_checks(cfg: DockerConfig, found: Docker | None) -> list[Check]:
    """The three log failures `--validate` waves through.

    All three have the same symptom — the daemon is fine and no container will
    start — and none of them mentions daemon.json in the error.
    """
    checks: list[Check] = []
    driver = cfg.log_driver

    if found is not None and found.log_plugins:
        # `none` is built into the daemon and never appears in the plugin list, so
        # the list is "drivers that certainly exist" rather than "all valid drivers".
        ok = driver in found.log_plugins or driver in BUILTIN_DRIVERS
        checks.append(Check(
            "the log driver exists",
            ok,
            f"'{driver}' is available on this daemon"
            if ok
            else f"this daemon has no '{driver}' plugin — every container would fail to start",
        ))

    spec = LOG_DRIVERS.get(driver)
    opts = cfg.log_opts()
    if spec is not None:
        unknown = [key for key in opts if key not in spec.opts]
        checks.append(Check(
            "the log options suit the driver",
            not unknown,
            f"{len(opts)} option{'s' if len(opts) != 1 else ''} accepted by {driver}"
            if not unknown
            else f"{driver} rejects: {', '.join(unknown)}",
        ))

    # Measured: "compress cannot be true when max-file is less than 2 or max-size is
    # not set". `--validate` is perfectly happy with it.
    if opts.get("compress") == "true":
        try:
            files = int(opts.get("max-file", "1"))
        except ValueError:
            files = 1
        ok = files >= 2 and bool(opts.get("max-size"))
        checks.append(Check(
            "compression is usable",
            ok,
            "compress needs max-file 2 or more and a max-size"
            if not ok
            else f"compressing rotated files, keeping {files}",
        ))

    if cfg.driver().rotates and not cfg.logs_are_capped():
        checks.append(Check(
            "container logs are capped",
            False,
            f"{driver} writes without a size limit — this is the disk-filling default",
        ))
    return checks


def _pool_checks(cfg: DockerConfig) -> list[Check]:
    """Address pools that parse but cannot produce a network.

    `--validate` parses the CIDR and stops there, so a `size` narrower than the base
    prefix passes and then yields no usable subnets.
    """
    checks: list[Check] = []
    for base, size in cfg.address_pools:
        try:
            network = ipaddress.ip_network(base, strict=False)
        except ValueError:
            checks.append(Check(f"address pool {base}", False, "not a valid network"))
            continue
        if size < network.prefixlen:
            checks.append(Check(
                f"address pool {base}",
                False,
                f"/{size} is wider than the {base} it is carved from",
            ))
        elif size > 30:
            checks.append(Check(
                f"address pool {base}", False, f"/{size} leaves no usable addresses"
            ))
        else:
            count = 2 ** (size - network.prefixlen)
            checks.append(Check(
                f"address pool {base}", True, f"{count} networks of /{size}"
            ))
    return checks


def _host_checks(cfg: DockerConfig, found: Docker | None) -> list[Check]:
    """`hosts` in daemon.json versus `-H` in the systemd unit.

    Both set the same thing, the daemon refuses to start with both, and the
    resulting error is about flag conflicts rather than about this file. Only
    reachable through a carried-over key, since the wizard never writes `hosts`.
    """
    if "hosts" not in cfg.extra:
        return []
    if found is None or not found.unit_sets_host:
        return [Check(
            "hosts does not conflict",
            True,
            "no systemd unit passing -H was found",
        )]
    return [Check(
        "hosts does not conflict",
        False,
        "the systemd unit already passes -H; with both, dockerd refuses to start "
        "(drop one, or override ExecStart)",
    )]


# ---------------------------------------------------------------------------
# Writing it
# ---------------------------------------------------------------------------


def write(cfg: DockerConfig, path: Path, *, sudo: bool) -> tuple[bool, str]:
    """Install the config at `path`, through sudo when the path needs root.

    Written to a temp file first and moved into place with `install`, so a daemon
    reading the file mid-write cannot see a half-written one — and so a failed sudo
    leaves the existing config untouched rather than truncated.
    """
    text = render.to_json(cfg)
    if not sudo:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            return False, str(exc)
        return True, str(path)

    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="devstuff-daemon-", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(text)
            staged = Path(handle.name)
    except OSError as exc:  # pragma: no cover
        return False, str(exc)

    try:
        mkdir = _run(["sudo", "install", "-d", "-m", "0755", str(path.parent)])
        if mkdir is None or mkdir.returncode != 0:
            detail = (mkdir.stderr.strip() if mkdir else "sudo did not run") or "sudo failed"
            return False, detail
        result = _run(
            ["sudo", "install", "-m", "0644", "-o", "root", "-g", "root", str(staged), str(path)]
        )
    finally:
        staged.unlink(missing_ok=True)

    if result is None:
        return False, "sudo did not run"
    if result.returncode != 0:
        return False, (result.stderr.strip() or "sudo install failed")
    return True, str(path)


def backup(path: Path, *, sudo: bool) -> tuple[Path | None, str]:
    """Copy the existing config aside. (backup path, message).

    A root-owned file needs sudo to copy as well as to replace, so this mirrors
    `write()` rather than using shutil.
    """
    if not path.exists():
        return None, "no existing config"
    from datetime import datetime

    dest = path.with_name(f"{path.name}.bak.{datetime.now():%Y%m%d-%H%M%S}")
    if not sudo:
        try:
            shutil.copy2(path, dest)
        except OSError as exc:
            return None, str(exc)
        return dest, str(dest)
    result = _run(["sudo", "cp", "-p", str(path), str(dest)])
    if result is None or result.returncode != 0:
        return None, (result.stderr.strip() if result else "sudo did not run") or "backup failed"
    return dest, str(dest)


def restart() -> tuple[bool, str]:
    """`systemctl restart docker`. The config is not read until this happens.

    Deliberately a restart and not a reload: `systemctl reload docker` re-reads only
    a subset of daemon.json, and which subset is not something to guess at in a
    wizard. The cost of the restart is stated by the caller before it runs.
    """
    result = _run(["sudo", "systemctl", "restart", "docker"], timeout=RESTART_TIMEOUT)
    if result is None:
        return False, "systemctl did not run"
    if result.returncode != 0:
        return False, (result.stderr.strip() or "restart failed")
    return True, "docker restarted"


def effective() -> dict[str, object] | None:
    """What the running daemon believes now — used to confirm a restart took.

    Reading it back is the only proof that the file reached the daemon; a config
    with a key the daemon ignores looks identical on disk to one it honoured.
    """
    result = _run(["docker", "info", "--format", "{{json .}}"])
    if result is None or result.returncode != 0:
        return None
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def add_to_group() -> tuple[bool, str]:
    """`usermod -aG docker $USER` — what makes docker usable without sudo.

    The new group is not in the current session's credentials, so this always needs
    a fresh login to take effect. The caller says so.
    """
    import getpass

    try:
        user = getpass.getuser()
    except (KeyError, OSError):  # pragma: no cover
        return False, "could not determine the current user"
    result = _run(["sudo", "usermod", "-aG", "docker", user])
    if result is None:
        return False, "sudo did not run"
    if result.returncode != 0:
        return False, (result.stderr.strip() or "usermod failed")
    return True, user


__all__ = [
    "Check",
    "Report",
    "add_to_group",
    "available",
    "backup",
    "effective",
    "restart",
    "sandbox",
    "verify",
    "version",
    "write",
]
