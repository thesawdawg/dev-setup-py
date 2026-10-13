"""Notes about which catalog tool manages — or could manage — another's installs.

`list` and the `install` picker annotate a tool with the manager that owns its
installs: derived from the install type for uvx/pip (uv) and npm (nvm) tools,
explicit via the `managed_by` field for anything else (python, pi).

"Actually managed" is answered two ways, matching how each manager records
ownership. The *type signal* reuses the probes `outdated` already trusts
(`uv tool list`, `npm ls -g` inside nvm), so list and outdated agree. The *path
signal* resolves the tool's executable and asks whether it lives under a root
the manager reports for itself (`uv tool dir` / `uv python dir`, `$NVM_DIR`) —
containment via `Path.is_relative_to` on resolved paths, never `startswith`,
so `uv/python-other` does not read as inside `uv/python`. A probe that cannot
answer is `unknown`, never silently `managed`: claiming management we did not
verify is the failure this module exists to prevent.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from rich.markup import escape

from dev_setup import generic, registry, ui
from dev_setup.base import Tool

# Install types whose tool records are owned by a catalog manager outright —
# no managed_by field needed.
AUTO_MANAGER = {"uvx": "uv", "pip": "uv", "npm": "nvm"}


@dataclass(frozen=True)
class ManagerNote:
    manager: str  # catalog key, e.g. "uv"
    state: str  # "managed" | "outside" | "can-manage" | "system-copy" | "unknown"
    path: str = ""  # resolved path, for outside / system-copy (when known)
    executable: str = ""  # for system-copy, e.g. "python3"
    why: str = ""  # for unknown: the probe's reason


class _Verdict(NamedTuple):
    managed: bool | None
    why: str = ""


def manager_of(tool: Tool) -> str | None:
    """The catalog key managing this tool's installs, or None if none applies.

    A managed_by typo or a key not in the registry produces no note at all —
    a wrong note is worse than none.
    """
    mgr = getattr(tool, "managed_by", "") or AUTO_MANAGER.get(tool.install_type, "")
    if not mgr or mgr == tool.key or not registry.exists(mgr):
        return None
    return mgr


def note_for(tool: Tool, installed: bool) -> ManagerNote | None:
    """Best-effort probe of who manages this tool's copy. Never raises."""
    try:
        mgr = manager_of(tool)
        if mgr is None:
            return None
        if installed:
            verdict = _is_managed(tool, mgr)
            if verdict.managed is True:
                return ManagerNote(mgr, "managed")
            if verdict.managed is False:
                found = _resolve_executable(tool, mgr)
                return ManagerNote(
                    mgr, "outside", path=os.path.realpath(found) if found else ""
                )
            return ManagerNote(mgr, "unknown", why=verdict.why)
        exe = _executable(tool)
        found = shutil.which(exe) if exe else None
        if exe and found:
            return ManagerNote(
                mgr, "system-copy", executable=exe, path=os.path.realpath(found)
            )
        return ManagerNote(mgr, "can-manage")
    except Exception:
        return None


def _is_managed(tool: Tool, mgr: str) -> _Verdict:
    """True/False when a probe answered, None plus `why` when it could not."""
    if AUTO_MANAGER.get(tool.install_type) == mgr:
        return _managed_by_type(tool, mgr)
    return _managed_by_path(tool, mgr)


def _executable(tool: Tool) -> str:
    """The binary PATH would name for this tool, or "" when no field provides one."""
    for cand in (getattr(tool, "version_cmd", ""), getattr(tool, "check_cmd", "")):
        if generic._is_simple_command(cand or ""):
            return cand
    return ""


# -- Type signal ---------------------------------------------------------------
# The same records `outdated` trusts: uv tools appear in `uv tool list`, nvm's
# npm packages in `npm ls -g` after sourcing nvm.sh. Membership means managed.


def _managed_by_type(tool: Tool, mgr: str) -> _Verdict:
    if mgr == "uv":
        answer = generic._uv_tool_versions()
        name = tool.pip_name
    elif mgr == "nvm":
        answer = _nvm_npm_globals()
        name = tool.npm_name
    else:
        return _Verdict(None, f"no way to check what {mgr} manages")
    if answer.data is None:
        if answer.why == f"{mgr} is not installed":
            return _Verdict(False, answer.why)
        return _Verdict(None, answer.why)
    return _Verdict(bool(name) and name in answer.data)


@generic._once
def _nvm_npm_globals() -> generic._Answer:
    """Package name -> version for nvm's global npm installs. One call per process.

    `npm ls -g` can exit non-zero yet print a valid JSON tree (extraneous or
    problem packages), so stdout is parsed whatever the return code; only
    unparseable output is a failed probe.
    """
    nvm_dir = _nvm_dir()
    if not (nvm_dir / "nvm.sh").exists():
        return generic._Answer(None, "nvm is not installed")
    try:
        r = generic._probe(
            ["bash", "-c", f'. "{nvm_dir}/nvm.sh" >/dev/null 2>&1 && npm ls -g --depth=0 --json'],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return generic._Answer(None, "couldn't list nvm's global npm packages")
    try:
        deps = json.loads(r.stdout or "{}").get("dependencies", {})
    except (json.JSONDecodeError, AttributeError):
        return generic._Answer(None, "couldn't list nvm's global npm packages")
    return generic._Answer(
        {name: (info.get("version", "") if isinstance(info, dict) else "") for name, info in deps.items()}
    )


# -- Path signal ---------------------------------------------------------------
# For explicit managed_by (python, pi): where the resolved executable sits.


def _managed_by_path(tool: Tool, mgr: str) -> _Verdict:
    exe = _executable(tool)
    if not exe:
        return _Verdict(None, "no executable to check")
    probe = _ROOT_PROBES.get(mgr)
    if probe is None:
        return _Verdict(None, f"no way to check what {mgr} manages")
    answer = probe()
    if answer.data is None:
        return _Verdict(None, answer.why)
    if not answer.data:
        return _Verdict(False)  # the manager itself is absent, so it manages nothing
    path = _resolve_executable(tool, mgr)
    if not path:
        return _Verdict(None, f"couldn't find {exe} on PATH")
    real = Path(os.path.realpath(path))
    return _Verdict(any(real.is_relative_to(root) for root in answer.data))


def _resolve_executable(tool: Tool, mgr: str) -> str:
    """Where `tool`'s binary resolves, or "". For nvm a direct `which` miss gets a
    second chance through an nvm-sourced shell — the binary may only exist once
    nvm has put its node bin dir on PATH."""
    exe = _executable(tool)
    if not exe:
        return ""
    path = shutil.which(exe)
    if path is None and mgr == "nvm":
        try:
            r = generic._probe(
                ["bash", "-c", f'. "{_nvm_dir()}/nvm.sh" 2>/dev/null; command -v {shlex.quote(exe)}'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                path = r.stdout.strip().splitlines()[-1]
        except Exception:
            pass
    return path or ""


@generic._once
def _uv_roots() -> generic._Answer:
    """Directories uv owns (tool envs, managed pythons), as resolved paths.

    Empty means "uv is not installed" — a definitive *outside*, distinct from
    a failed probe (None) which is *unknown*.
    """
    uv = shutil.which("uv")
    if not uv:
        return generic._Answer({})
    roots: dict[str, str] = {}
    for sub in ("tool", "python"):
        try:
            r = generic._probe(
                [uv, sub, "dir"], capture_output=True, text=True, timeout=10
            )
        except Exception:
            return generic._Answer(None, f"couldn't run `uv {sub} dir`")
        if r.returncode != 0 or not r.stdout.strip():
            return generic._Answer(None, f"`uv {sub} dir` failed")
        roots[os.path.realpath(r.stdout.strip().splitlines()[0])] = ""
    return generic._Answer(roots)


@generic._once
def _nvm_roots() -> generic._Answer:
    nvm_dir = _nvm_dir()
    if nvm_dir.is_dir():
        return generic._Answer({os.path.realpath(nvm_dir): ""})
    return generic._Answer({})


def _nvm_dir() -> Path:
    return Path(os.environ.get("NVM_DIR") or Path.home() / ".nvm")


_ROOT_PROBES = {"uv": _uv_roots, "nvm": _nvm_roots}


# -- Rendering -----------------------------------------------------------------


def list_line(note: ManagerNote, tool: Tool) -> str:
    """A second description-cell line for `devstuff list`; "" for an unknown state.

    `outside` carries no `devstuff install` remedy on purpose: install_cmd
    returns early on is_installed(), so that advice would be false. `system-copy`
    can carry it because the tool reads not-installed, so install actually runs.
    """
    mgr = escape(note.manager)
    if note.state == "managed":
        return f"[{ui.GRAY}]  ✓ managed by {mgr}[/]"
    if note.state == "outside":
        path = f" ({escape(note.path)})" if note.path else ""
        return f"[{ui.AMBER}]  ⇢ installed outside {mgr}{path}; {mgr} can manage it[/]"
    if note.state == "can-manage":
        return f"[{ui.GRAY}]  ⇢ {mgr} can manage this[/]"
    if note.state == "system-copy":
        return (
            f"[{ui.AMBER}]  ⇢ {escape(note.executable)} at {escape(note.path)} isn't "
            f"managed by {mgr}; `devstuff install {escape(tool.key)}` lets {mgr} manage it[/]"
        )
    if note.state == "unknown":
        why = f" ({escape(note.why)})" if note.why else ""
        return f"[{ui.GRAY}]  ? {mgr}-managed tool; couldn't confirm{why}[/]"
    return ""


def picker_hint(note: ManagerNote, tool: Tool) -> tuple[str, str | None]:
    """(title suffix, disabled-text override) for the install picker.

    Plain text only — picker titles aren't Rich markup, so nothing is escaped.
    `unknown` returns no override: the default "installed" is the honest row.
    """
    mgr = note.manager
    if note.state == "managed":
        return "", f"installed · managed by {mgr}"
    if note.state == "outside":
        return "", f"installed outside {mgr}"
    if note.state == "can-manage":
        return f"  (via {mgr})", None
    if note.state == "system-copy":
        return f"  ({note.executable} found outside {mgr})", None
    return "", None
