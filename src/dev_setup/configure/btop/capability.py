"""The `cap_perfmon` offer — CPU package watts and Intel GPU stats need it.

Upstream's `make setcap` is `setcap cap_perfmon=+ep` on the binary, documented as
required for those readings and preferred over setuid. The Debian package ships
no postinst and no capability, and an apt upgrade replaces the binary — which
drops the capability — so the wizard offers it after a save and says that.

Three things bound what this module does:

- **It verifies the capability is set, nothing more.** `getcap` after `setcap`
  is the whole check — containers and VMs often hide the power counters, so
  whether watts then appear is not knowable here.
- **It refuses some binaries.** `setcap` on a binary others can replace — not
  root-owned, or group/other-writable — is a privilege hole, not a fix; the
  refusal names why and prints the command for the user to judge.
- **Sudo failures are failures**, surfaced through `generic._run` like every
  other state-changing command; verification after the fact is a re-read, not a
  trust of the exit code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from dev_setup.generic import _run

CAP = "cap_perfmon"


def binary() -> str | None:
    """The resolved realpath of the btop on PATH."""
    path = shutil.which("btop")
    return str(Path(path).resolve()) if path else None


def tool(name: str) -> str | None:
    """setcap/getcap live in sbin, which a non-root PATH may not have."""
    found = shutil.which(name)
    if found:
        return found
    for directory in ("/usr/sbin", "/sbin"):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def has_perfmon(path: str) -> bool | None:
    """Whether `cap_perfmon` is set on the binary. `None` = cannot tell."""
    getcap = tool("getcap")
    if getcap is None:
        return None
    try:
        result = subprocess.run(
            [getcap, path], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # getcap prints e.g. `/usr/bin/btop cap_perfmon=ep`; nothing when unset.
    return f"{CAP}=" in result.stdout


def refusal(path: str) -> str | None:
    """Why a grant must not run on this binary. `None` = no objection."""
    if tool("setcap") is None:
        return "setcap was not found (it lives in the libcap2-bin package, in sbin)"
    try:
        st = os.stat(path)
    except OSError:
        return f"cannot stat {path}"
    if st.st_uid != 0:
        return (
            "the binary is not owned by root — a capability on a binary you can "
            "replace is a privilege hole"
        )
    if st.st_mode & 0o022:
        return "the binary is writable by group or others — a capability on it is a privilege hole"
    return None


def grant(path: str) -> None:
    """`sudo setcap cap_perfmon=+ep <path>`, then verify with getcap."""
    _run(["sudo", tool("setcap") or "setcap", f"{CAP}=+ep", path])
    if has_perfmon(path) is not True:
        raise RuntimeError("setcap ran, but getcap does not show cap_perfmon — not verified")


def revoke(path: str) -> None:
    """`sudo setcap -r <path>`, then verify the capability is gone."""
    _run(["sudo", tool("setcap") or "setcap", "-r", path])
    if has_perfmon(path) is not False:
        raise RuntimeError("setcap -r ran, but getcap still shows cap_perfmon — not verified")


__all__ = ["binary", "grant", "has_perfmon", "refusal", "revoke", "tool"]
