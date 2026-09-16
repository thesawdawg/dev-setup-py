"""Ask the node. One check, run through the functions' own transport.

This is the local form of the rule the starship configurator states as "preview with the
real binary": the wizard's connection test runs `lib.sh`'s `pve_selftest` in bash, with
the same exported environment a `pve-*` function gets. A second implementation in
`urllib` would test something adjacent to what the user is about to do — TLS
verification, proxy handling and the `-K -` credential path all differ between the two —
so a pass would not mean what it appears to mean (SD-4).

`pve-check` is the same code reached from the command line, which is why there is no
third copy of any of this.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from dev_setup.configure.proxmox import render
from dev_setup.configure.proxmox.model import Profile

#: Generous: an unreachable host spends ConnectTimeout per ssh call, and the API probe
#: makes four requests. The point of the ceiling is that the wizard cannot hang, not that
#: it is quick.
TIMEOUT = 90


@dataclass
class Check:
    status: str  # ok | warn | fail
    name: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status != "fail"


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.checks) and all(c.ok for c in self.checks)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    def node_name(self) -> str:
        """The name the node calls itself, when the check managed to ask."""
        for check in self.checks:
            if check.name == "node name" and check.status == "ok":
                return check.detail
        return ""


def selftest(profile: Profile, *, timeout: int = TIMEOUT) -> Report:
    script = "\n".join([
        "set -u",
        render.export_shell(profile),
        'if ! . "$PVE_LIB"; then echo "cannot load $PVE_LIB" >&2; exit 1; fi',
        "pve_selftest",
    ])
    try:
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Report(error=f"The check did not finish within {timeout}s.")
    except (OSError, subprocess.SubprocessError) as exc:
        return Report(error=str(exc))

    report = Report()
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            report.checks.append(Check(status=parts[0], name=parts[1], detail=parts[2]))
    if not report.checks:
        detail = (result.stderr or "").strip().splitlines()
        report.error = detail[-1] if detail else "The check produced no results."
    return report
