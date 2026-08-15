from __future__ import annotations

import functools
import hashlib
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path

from dev_setup import compat, platforms, verbose
from dev_setup.base import Tool

# Auto-inferred requires per install type (re-derived on load, not persisted)
AUTO_REQUIRES = {
    "npm": ["nvm"],
    "pip": ["uv"],
    "uvx": ["uv"],
}

# dataclass field name -> catalog YAML key (only where they differ)
_YAML_KEY = {"install_type": "type"}
# fields that are identity/metadata, always persisted
_ALWAYS_PERSIST = ("name", "description", "category", "install_type")
# fields never read from / written to the catalog. `unsupported_reason`/`alternative`
# are decided per host by catalog.resolve_for_platform and set by the registry, so
# they must never be written back into a catalog file.
_NON_CATALOG = (
    "key", "builtin",
    "unsupported_reason", "alternative", "unsupported_inferred", "compat_findings",
)


@dataclass
class UpdateStatus:
    """Best-effort result of probing whether a newer version is available.

    `available` is None when the install type has no reliable way to check
    (script/bash) or the probe itself failed (offline, missing tool, etc).
    """

    current: str = ""
    latest: str = ""
    available: bool | None = None


def _run(cmd: list, *, cwd: Path | None = None) -> None:
    """Run a state-changing command (install/remove/update). Raises RuntimeError on failure.

    Streams output when verbose, captures when not — captured output is discarded on
    success and surfaced as the exception message on failure, which is the only reason
    quiet mode can say anything useful about what went wrong.
    """
    verbose.command(cmd, cwd=cwd)
    env = platforms.child_env()
    if verbose.enabled():
        try:
            subprocess.run(cmd, check=True, cwd=cwd, env=env)
        except subprocess.CalledProcessError as e:
            # The output already streamed past; repeating the whole argv in the error
            # adds nothing the user can't see directly above it.
            raise RuntimeError(f"exit code {e.returncode}") from e
    else:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=cwd, env=env)
        except subprocess.CalledProcessError as e:
            msg = e.stderr.strip() if e.stderr else f"exit code {e.returncode}"
            raise RuntimeError(msg) from e


def _probe(cmd: list, *, log_at: int = verbose.TRACE, **kwargs) -> subprocess.CompletedProcess:
    """Run a command whose output we capture rather than stream, and don't raise on.

    Used for read-only probes (version checks, dpkg queries, …) and for the few
    best-effort actions whose failure is deliberately ignored. Verbosity only adds
    logging here, and by default only at -vv: probes fire constantly — one or more per
    tool on every `list` — and would bury the actual work at -v. `log_at=verbose.VERBOSE`
    marks the ones that are real actions the user should see at -v.
    """
    kwargs.setdefault("capture_output", True)
    env = platforms.child_env()
    if env is not None:
        kwargs.setdefault("env", env)
    verbose.command(cmd, cwd=kwargs.get("cwd"), minimum=log_at)
    proc = subprocess.run(cmd, **kwargs)
    if verbose.enabled(verbose.TRACE):
        out = "".join(
            part for part in (proc.stdout, proc.stderr) if isinstance(part, str)
        )
        verbose.result(proc.returncode, out)
    return proc


@dataclass
class GenericTool(Tool):
    key: str = ""
    name: str = ""
    description: str = ""
    category: str = "custom"
    install_type: str = "unknown"
    check_cmd: str = ""
    version_cmd: str = ""
    npm_name: str = ""
    pip_name: str = ""
    # uvx/pip only. `uv tool install` exposes console scripts of the *requested*
    # package alone, so a package whose entry points live in a dependency (ansible's
    # live in ansible-core) needs uv_executables_from or it installs nothing usable.
    uv_with: list | None = None
    uv_executables_from: list | None = None
    uv_python: str = ""
    git_url: str = ""
    git_install_cmd: str = ""
    git_remove_cmd: str = ""
    # `packages` is the canonical field for the system install type; `apt_packages`
    # is the original spelling and still honoured. Read them through
    # `system_packages`, never directly.
    packages: str = ""
    apt_packages: str = ""
    script_url: str = ""
    sha256: str = ""
    install_script: str = ""
    remove_script: str = ""
    help_cmd: str = ""
    docs_url: str = ""
    requires: list | None = None
    builtin: bool = False
    # Non-empty when this host cannot install the tool at all. Set by the registry
    # from the catalog's `platforms:`/`requires_traits:` resolution, or — for entries
    # that declared nothing — from scanning the install source (`compat.py`).
    unsupported_reason: str = ""
    alternative: str = ""
    unsupported_inferred: bool = False
    compat_findings: list | None = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.key
        if self.requires is None:
            self.requires = list(AUTO_REQUIRES.get(self.install_type, []))
        # Normalise to lists so the installer can iterate without None checks. Both
        # stay falsy when unset, so to_dict() still omits them.
        if self.uv_with is None:
            self.uv_with = []
        if self.uv_executables_from is None:
            self.uv_executables_from = []

    @classmethod
    def from_dict(cls, data: dict, key: str) -> GenericTool:
        kwargs = {
            f.name: data.get(_YAML_KEY.get(f.name, f.name), f.default)
            for f in fields(cls)
            if f.name not in _NON_CATALOG and f.name != "requires"
        }
        # `requires` default is None (auto-derive); dict may carry an explicit list
        kwargs["requires"] = data.get("requires")
        return cls(key=key, **kwargs)

    def to_dict(self) -> dict:
        d: dict = {}
        for f in fields(self):
            if f.name in _NON_CATALOG or f.name == "requires":
                continue
            val = getattr(self, f.name)
            if f.name in _ALWAYS_PERSIST or val:
                d[_YAML_KEY.get(f.name, f.name)] = val
        # Only persist explicit requires — auto-inferred ones are re-derived on load
        if self.requires is not None and self.requires != AUTO_REQUIRES.get(self.install_type, []):
            d["requires"] = self.requires
        return d

    def save(self) -> None:
        from dev_setup import catalog
        catalog.save_user_tool(self.key, self.to_dict())

    @property
    def system_packages(self) -> list[str]:
        """Packages to hand the host package manager, from either spelling."""
        return (self.packages or self.apt_packages).split()

    # -- Strategy dispatch ----------------------------------------------------

    def is_installed(self) -> bool:
        if self.check_cmd:
            return _check_cmd_installed(self.check_cmd, install_type=self.install_type)
        checker = _CHECKERS.get(self.install_type)
        return checker(self) if checker else False

    def install(self) -> str | None:
        self._require_supported()
        installer = _INSTALLERS.get(self.install_type)
        if installer is None:
            raise RuntimeError(f"Unsupported install type: {self.install_type!r}")
        installer(self)
        return self.get_version() or None

    def remove(self) -> None:
        remover = _REMOVERS.get(self.install_type)
        if remover is None:
            raise RuntimeError(f"Unsupported remove type: {self.install_type!r}")
        remover(self)

    def _require_supported(self) -> None:
        """Refuse an install this host can't complete, before anything is run.

        Enforced here rather than only in the command layer because every other
        entry point — a configurator installing a prerequisite, the agent's catalog
        bridge, `update` re-running an installer — goes through these methods too.

        `--force` overrides a reason that was *inferred* from the install source,
        because inference can be wrong. It never overrides one the catalog declared:
        that is an authored statement of fact, not a guess.
        """
        if self.supported:
            return
        if self.unsupported_inferred and compat.forced():
            from dev_setup import ui
            ui.warn(
                f"--force: installing {self.name} anyway, despite {self.unsupported_reason}"
            )
            return
        msg = f"{self.name} is not available on this platform: {self.unsupported_reason}"
        if self.alternative:
            msg += f". Try '{self.alternative}' instead: devstuff install {self.alternative}"
        raise RuntimeError(msg)

    def update(self, version: str | None = None) -> str | None:
        """Update an already-installed tool to the latest (or a specified) version."""
        self._require_supported()
        updater = _UPDATERS.get(self.install_type)
        if updater is None:
            raise RuntimeError(f"Unsupported update type: {self.install_type!r}")
        updater(self, version)
        return self.get_version() or None

    def check_for_update(self) -> UpdateStatus:
        """Best-effort probe for a newer version. Never raises."""
        checker = _UPDATE_CHECKERS.get(self.install_type)
        if checker is None:
            return UpdateStatus()
        try:
            return checker(self)
        except Exception:
            return UpdateStatus()

    def get_version(self) -> str:
        # Prefer explicit version_cmd; fall through to check_cmd / type-derived cmd
        cmd = self.version_cmd or (
            self.check_cmd if _is_simple_command(self.check_cmd or "") else ""
        ) or _type_cmd(self)

        if cmd and shutil.which(cmd):
            for flag in ["--version", "-v", "version"]:
                try:
                    r = _probe([cmd, flag], capture_output=True, text=True, timeout=5)
                    if r.returncode == 0 and r.stdout.strip():
                        return r.stdout.strip().splitlines()[0]
                except Exception:
                    pass
            return "installed"

        # Complex check_cmd (shell expression) — probe via login shell using tool key
        if self.check_cmd and not _is_simple_command(self.check_cmd):
            return _bash_version(self.key)

        return ""


# -- Install strategies --------------------------------------------------------


def _install_npm(tool: GenericTool) -> None:
    if not tool.npm_name:
        raise RuntimeError("npm_name not set")
    with verbose.step(f"Installing {tool.name} via npm..."):
        _run(["bash", "-lc", f"{_npm_init()} && npm install -g {shlex.quote(tool.npm_name)}"])


def _uv_install_flags(tool: GenericTool) -> list[str]:
    """Catalog uv_* fields as `uv tool install` flags.

    uv records all of these in the tool's uv-receipt.toml and re-applies them on
    `uv tool upgrade`, so they only need passing at install time.
    """
    flags: list[str] = []
    if tool.uv_python:
        flags += ["--python", tool.uv_python]
    for pkg in tool.uv_with or []:
        flags += ["--with", pkg]
    for pkg in tool.uv_executables_from or []:
        flags += ["--with-executables-from", pkg]
    return flags


def _install_uvx(tool: GenericTool) -> None:
    if not tool.pip_name:
        raise RuntimeError("pip_name not set")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required to install uvx packages. "
            "Install it first: devstuff install uv"
        )
    with verbose.step(f"Installing {tool.name} via uvx..."):
        _run([uv, "tool", "install"] + _uv_install_flags(tool) + [tool.pip_name])


def _install_git(tool: GenericTool) -> None:
    if not tool.git_url:
        raise RuntimeError("git_url not set")
    dest = _git_clone_dest(tool.git_url)
    with verbose.step(f"Cloning {tool.name}..."):
        _run(["git", "clone", "--depth=1", tool.git_url, str(dest)])
    if tool.git_install_cmd:
        with verbose.step("Running install command..."):
            _run(["bash", "-c", tool.git_install_cmd], cwd=dest)


def _install_system(tool: GenericTool) -> None:
    from dev_setup import ui
    packages = tool.system_packages
    if not packages:
        raise RuntimeError("packages not set")
    pm = platforms.package_manager()
    ui.info(f"Installing {tool.name} via {pm.id}...")
    _refresh_package_index()
    _run(platforms.escalate(pm.install_argv(packages)))


def _install_script_url(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.script_url:
        raise RuntimeError("script_url not set")
    ui.info(f"Running install script for {tool.name}...")
    script = _download_script(tool.script_url, expected_sha256=tool.sha256)
    # The body of a `curl | sh` installer does not exist until now, so this is the
    # first opportunity to check it against the host — and the last one before it
    # starts making changes.
    _check_downloaded_script(tool, script)
    _run_bash_script(script)


def _check_downloaded_script(tool: GenericTool, script: str) -> None:
    """Refuse a just-downloaded installer this host cannot run.

    Skipped when the catalog already declared something about this platform, for the
    same reason the registry's scan is (`compat.should_scan`) — and skipped under
    `--force`, which is the escape hatch for a wrong inference.
    """
    if tool.compat_findings is None:
        return  # the catalog spoke for this host; don't second-guess it
    findings = compat.blocking(compat.scan_script(script))
    if not findings:
        return
    if compat.forced():
        from dev_setup import ui
        ui.warn(f"--force: running {tool.name}'s installer anyway, despite "
                f"{compat.summarise(findings)}")
        return
    raise RuntimeError(
        f"the downloaded installer is not compatible with this platform: "
        f"{compat.summarise(findings)}. Re-run with --force to try anyway."
    )


def _install_bash(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.install_script:
        raise RuntimeError("install_script not set")
    ui.info(f"Installing {tool.name}...")
    _run_bash_script(tool.install_script)


# -- Update strategies --------------------------------------------------------


def _update_npm(tool: GenericTool, version: str | None) -> None:
    if not tool.npm_name:
        raise RuntimeError("npm_name not set")
    target = f"{tool.npm_name}@{version or 'latest'}"
    with verbose.step(f"Updating {tool.name} via npm..."):
        _run(["bash", "-lc", f"{_npm_init()} && npm install -g {shlex.quote(target)}"])


def _update_uvx(tool: GenericTool, version: str | None) -> None:
    if not tool.pip_name:
        raise RuntimeError("pip_name not set")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required to update uvx packages. "
            "Install it first: devstuff install uv"
        )
    # Both paths go through `uv tool install`, not `uv tool upgrade`.
    #
    # `uv tool upgrade` takes a tool *name*, not a requirement, so the pinned form
    # ("pkg==1.2.3") was read as the whole name and always failed. And it is a no-op
    # on an already-pinned tool ("Nothing to upgrade"), which would leave a user who
    # ever pinned with no way back to latest through devstuff. `install pkg@latest`
    # re-resolves *and* clears the pin in one call — measured against uv 0.11.21.
    #
    # Passing the uv_* flags on every update is deliberate: this writes a fresh
    # receipt, and re-deriving from the catalog means a newly added uv_with or
    # uv_executables_from takes effect on update rather than only on reinstall.
    target = f"{tool.pip_name}=={version}" if version else f"{tool.pip_name}@latest"
    cmd = [uv, "tool", "install", "--force"] + _uv_install_flags(tool) + [target]

    if version:
        from dev_setup import ui
        ui.warn(
            f"Pinning {tool.name} to {version}. It stays there until the next "
            f"'devstuff update {tool.key}' without --version, which moves it back "
            f"to the latest release."
        )

    with verbose.step(f"Updating {tool.name} via uv tool install..."):
        _run(cmd)


def _update_system(tool: GenericTool, version: str | None) -> None:
    from dev_setup import ui
    packages = tool.system_packages
    if not packages:
        raise RuntimeError("packages not set")
    pm = platforms.package_manager()
    _refresh_package_index()
    # upgrade_argv raises for a pin the manager cannot express (pacman) or for a pin
    # spread over several packages — both before anything is run.
    argv = pm.upgrade_argv(packages, version=version)
    if version:
        ui.info(f"Updating {tool.name} to version {version} via {pm.id}...")
    else:
        ui.info(f"Updating {tool.name} via {pm.id}...")
    _run(platforms.escalate(argv))


def _update_git(tool: GenericTool, version: str | None) -> None:
    if version:
        raise RuntimeError(
            "Version pinning is not supported for git tools (shallow clone). "
            f"Reinstall instead: devstuff remove {tool.key} && devstuff install {tool.key}"
        )
    if not tool.git_url:
        raise RuntimeError("git_url not set")
    dest = _git_clone_dest(tool.git_url)
    if not dest.exists():
        raise RuntimeError(f"{tool.name} clone not found at {dest}")
    with verbose.step(f"Pulling latest {tool.name}..."):
        _run(["git", "-C", str(dest), "pull"])
        if tool.git_install_cmd:
            _run(["bash", "-c", tool.git_install_cmd], cwd=dest)


def _update_script_url(tool: GenericTool, version: str | None) -> None:
    if version:
        raise RuntimeError("Version pinning is not supported for 'script' tools.")
    _install_script_url(tool)


def _update_bash(tool: GenericTool, version: str | None) -> None:
    if version:
        raise RuntimeError("Version pinning is not supported for 'bash' tools.")
    _install_bash(tool)


# -- Update-availability checks -------------------------------------------------
# Best-effort: each function returns UpdateStatus() (all-empty/unknown) rather
# than raising, so a probe failure just shows as "unknown" to the caller.


def _npm_installed_version(pkg: str) -> str:
    import json
    try:
        r = _probe(
            ["bash", "-lc", f"{_npm_init()} && npm list -g --depth=0 --json {shlex.quote(pkg)}"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout or "{}")
        return data.get("dependencies", {}).get(pkg, {}).get("version", "")
    except Exception:
        return ""


def _npm_latest_version(pkg: str) -> str:
    try:
        r = _probe(
            ["bash", "-lc", f"{_npm_init()} && npm view {shlex.quote(pkg)} version"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _check_update_npm(tool: GenericTool) -> UpdateStatus:
    if not tool.npm_name:
        return UpdateStatus()
    current = _npm_installed_version(tool.npm_name)
    latest = _npm_latest_version(tool.npm_name)
    if not latest:
        return UpdateStatus(current=current)
    return UpdateStatus(current=current, latest=latest, available=bool(current) and current != latest)


def _uv_tool_current_version(pkg: str) -> str:
    uv = shutil.which("uv")
    if not uv:
        return ""
    try:
        r = _probe(
            [uv, "tool", "list", "--color", "never"], capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return ""
    for line in r.stdout.splitlines():
        if line.startswith((" ", "-", "\t")):
            continue  # sub-lines (installed executables) under each tool
        m = re.match(rf"^{re.escape(pkg)}\s+v?([\w.\-+]+)", line.strip())
        if m:
            return m.group(1)
    return ""


@functools.lru_cache(maxsize=1)
def _uv_outdated_map() -> dict[str, str]:
    """Package name -> latest version, for every outdated `uv tool`. One call per process."""
    uv = shutil.which("uv")
    if not uv:
        return {}
    try:
        r = _probe(
            [uv, "tool", "list", "--outdated", "--color", "never"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return {}
    result: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if line.startswith((" ", "-", "\t")):
            continue
        m = re.match(r"^(\S+)\s+v?[\w.\-+]+\s*\[latest:\s*([\w.\-+]+)\]", line.strip())
        if m:
            result[m.group(1)] = m.group(2)
    return result


def _check_update_uvx(tool: GenericTool) -> UpdateStatus:
    if not tool.pip_name or not shutil.which("uv"):
        return UpdateStatus()
    current = _uv_tool_current_version(tool.pip_name)
    latest = _uv_outdated_map().get(tool.pip_name, "")
    if not latest:
        return UpdateStatus(current=current, available=False if current else None)
    return UpdateStatus(current=current, latest=latest, available=True)


def _check_update_system(tool: GenericTool) -> UpdateStatus:
    # dpkg-query/apt-cache only exist on the Debian-family path (including Termux,
    # whose apt is Debian's). Everywhere else this returns "unknown" rather than
    # guessing — the same contract the script/bash types already have.
    packages = tool.system_packages
    pm = platforms.current().package_manager
    if not packages or pm is None or pm.query[0] != "dpkg":
        return UpdateStatus()
    pkg = packages[0]
    try:
        r = _probe(
            ["dpkg-query", "-W", "-f=${Version}", pkg], capture_output=True, text=True, timeout=10,
        )
        current = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        current = ""
    try:
        r = _probe(["apt-cache", "policy", pkg], capture_output=True, text=True, timeout=10)
    except Exception:
        return UpdateStatus(current=current)
    candidate = ""
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
            break
    if not candidate or candidate == "(none)":
        return UpdateStatus(current=current)
    return UpdateStatus(current=current, latest=candidate, available=bool(current) and current != candidate)


def _check_update_git(tool: GenericTool) -> UpdateStatus:
    if not tool.git_url:
        return UpdateStatus()
    dest = _git_clone_dest(tool.git_url)
    if not dest.exists():
        return UpdateStatus()
    try:
        r = _probe(
            ["git", "-C", str(dest), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        current = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        current = ""
    try:
        r = _probe(
            ["git", "ls-remote", tool.git_url, "HEAD"], capture_output=True, text=True, timeout=10,
        )
        remote_full = r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else ""
    except Exception:
        remote_full = ""
    if not remote_full:
        return UpdateStatus(current=current)
    latest = remote_full[:7]
    if not current:
        return UpdateStatus(latest=latest)
    return UpdateStatus(current=current, latest=latest, available=not remote_full.startswith(current))


_UPDATE_CHECKERS: dict[str, Callable[[GenericTool], UpdateStatus]] = {
    "npm": _check_update_npm,
    "pip": _check_update_uvx,
    "uvx": _check_update_uvx,
    "system": _check_update_system,
    "apt": _check_update_system,
    "git": _check_update_git,
}


# -- Remove strategies -----------------------------------------------------------


def _remove_npm(tool: GenericTool) -> None:
    with verbose.step(f"Removing {tool.name}..."):
        _run(["bash", "-lc", f"{_npm_init()} && npm uninstall -g {shlex.quote(tool.npm_name)}"])


def _remove_uvx(tool: GenericTool) -> None:
    # An explicit remove_script wins, mirroring _remove_apt. Without this, a tool that
    # shares a pip_name with another entry (ansible-vault shares ansible's) would
    # uninstall the whole shared tool environment out from under it.
    if tool.remove_script:
        _run_bash_script(tool.remove_script)
        return
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required to remove uvx packages. "
            "Install it first: devstuff install uv"
        )
    with verbose.step(f"Removing {tool.name}..."):
        _run([uv, "tool", "uninstall", tool.pip_name])


def _remove_git(tool: GenericTool) -> None:
    dest = _git_clone_dest(tool.git_url)
    if tool.git_remove_cmd:
        # Best-effort: the clone directory is removed below either way, so a remove
        # command that fails must not abort the removal.
        with verbose.step("Running remove command..."):
            _probe(["bash", "-c", tool.git_remove_cmd], cwd=dest, log_at=verbose.VERBOSE)
    if dest.exists():
        shutil.rmtree(dest)


def _remove_system(tool: GenericTool) -> None:
    from dev_setup import ui
    ui.info(f"Removing {tool.name}...")
    if tool.remove_script:
        _run_bash_script(tool.remove_script)
        return
    pm = platforms.package_manager()
    _run(platforms.escalate(pm.remove_argv(tool.system_packages)))


def _remove_script_url(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.remove_script:
        raise RuntimeError(
            "No remove script defined for this script-installed package. "
            "Remove manually then run: devstuff delete " + tool.key
        )
    ui.info(f"Removing {tool.name}...")
    _run_bash_script(tool.remove_script)


def _remove_bash(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.remove_script:
        raise RuntimeError(
            f"No remove script defined for '{tool.key}'. "
            "Remove manually then run: devstuff delete " + tool.key
        )
    ui.info(f"Removing {tool.name}...")
    _run_bash_script(tool.remove_script)


# -- Installed-state strategies ---------------------------------------------------


def _installed_npm(tool: GenericTool) -> bool:
    return bool(tool.npm_name) and _npm_global_installed(tool.npm_name)


def _installed_uvx(tool: GenericTool) -> bool:
    return bool(tool.pip_name) and shutil.which(tool.pip_name) is not None


def _installed_git(tool: GenericTool) -> bool:
    return bool(tool.git_url) and _git_clone_dest(tool.git_url).exists()


def _installed_system(tool: GenericTool) -> bool:
    """Ask the host package manager whether the first named package is installed.

    Runs through `_probe` like every other read-only check, so `-vv` sees it — this
    fires once per system-type tool on every `devstuff list`.
    """
    packages = tool.system_packages
    pm = platforms.current().package_manager
    if not packages or pm is None:
        return False
    argv = pm.query_argv(packages[0])
    if shutil.which(argv[0]) is None:
        return False
    try:
        r = _probe(argv, capture_output=True, text=True, timeout=15)
    except Exception:
        return False
    return pm.query_ok(packages[0], r.returncode, r.stdout or "")


# "apt" is the original name for the system-package type and stays a first-class
# alias of "system" in every dispatch table — user catalogs are full of it, and the
# handlers no longer assume apt in any case.
_INSTALLERS: dict[str, Callable[[GenericTool], None]] = {
    "npm": _install_npm,
    "pip": _install_uvx,
    "uvx": _install_uvx,
    "git": _install_git,
    "system": _install_system,
    "apt": _install_system,
    "script": _install_script_url,
    "bash": _install_bash,
}

_REMOVERS: dict[str, Callable[[GenericTool], None]] = {
    "npm": _remove_npm,
    "pip": _remove_uvx,
    "uvx": _remove_uvx,
    "git": _remove_git,
    "system": _remove_system,
    "apt": _remove_system,
    "script": _remove_script_url,
    "bash": _remove_bash,
}

_CHECKERS: dict[str, Callable[[GenericTool], bool]] = {
    "npm": _installed_npm,
    "pip": _installed_uvx,
    "uvx": _installed_uvx,
    "git": _installed_git,
    "system": _installed_system,
    "apt": _installed_system,
}

_UPDATERS: dict[str, Callable[[GenericTool, str | None], None]] = {
    "npm": _update_npm,
    "pip": _update_uvx,
    "uvx": _update_uvx,
    "git": _update_git,
    "system": _update_system,
    "apt": _update_system,
    "script": _update_script_url,
    "bash": _update_bash,
}


# -- Helpers ----------------------------------------------------------------------


def _npm_global_installed(pkg: str) -> bool:
    try:
        r = _probe(
            ["bash", "-lc", f"{_npm_init()} && npm list -g --depth=0 {shlex.quote(pkg)}"],
            capture_output=True,
            text=True,
        )
        return pkg in r.stdout
    except Exception:
        return False


def _refresh_package_index() -> None:
    """Refresh the host package manager's lists, if it has a separate command for it.

    Deliberately non-fatal: a failing mirror shouldn't stop an install of a package
    that may already be cached. Termux's `pkg` returns None here — its install verb
    already does mirror selection and a cache refresh of its own, so a second pass
    would only re-run the mirror probe.
    """
    pm = platforms.current().package_manager
    argv = pm.refresh_argv() if pm else None
    if argv is None:
        return
    _probe(platforms.escalate(argv), log_at=verbose.VERBOSE)


def _npm_init() -> str:
    return '. "$HOME/.nvm/nvm.sh" 2>/dev/null || true'


def _git_clone_dest(url: str) -> Path:
    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    return Path.home() / ".local" / "share" / "devstuff" / repo_name


def _download_script(url: str, *, expected_sha256: str = "") -> str:
    """Download a script over HTTPS and optionally verify its sha256."""
    import urllib.request

    verbose.log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read()
    verbose.trace(f"downloaded {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}")

    if expected_sha256:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256.lower():
            raise RuntimeError(
                f"Checksum mismatch for {url}\n"
                f"  expected: {expected_sha256.lower()}\n"
                f"  actual:   {actual}\n"
                "The script may have changed upstream — refusing to run it."
            )

    return data.decode("utf-8")


def _run_bash_script(script: str) -> None:
    import os
    import tempfile

    # Every script body gets the platform prelude ($DEVSTUFF_SUDO, $DEVSTUFF_BIN,
    # $DEVSTUFF_PKG_INSTALL, …) so a catalog entry can be written once and adapt,
    # instead of hardcoding `sudo apt-get` and failing on Termux/Fedora/Alpine.
    # It goes *after* any shebang so the first line stays the first line.
    body = platforms.with_prelude(script)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(body)
        tmp = f.name
    verbose.trace(f"script → {tmp}")
    verbose.block(body)
    try:
        # -x at -vv traces each expanded command to stderr as the script runs, which is
        # the only way to see where a downloaded installer actually failed.
        _run(["bash", *(["-x"] if verbose.enabled(verbose.TRACE) else []), tmp])
    finally:
        os.unlink(tmp)


def _type_cmd(tool: GenericTool) -> str:
    t = tool.install_type
    if t == "npm":
        return tool.npm_name
    if t in ("pip", "uvx"):
        return tool.pip_name
    return ""


def _is_simple_command(cmd: str) -> bool:
    return bool(cmd) and all(c not in cmd for c in " \t\n;&|$`'\"()<>")


def _bash_version(key: str) -> str:
    for flag in ["--version", "-v", "version"]:
        try:
            r = _probe(
                [
                    "bash", "-lc",
                    f'. "$HOME/.nvm/nvm.sh" 2>/dev/null; {shlex.quote(key)} {flag} 2>/dev/null',
                ],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().splitlines()[0]
        except Exception:
            pass
    return ""


def _check_cmd_installed(cmd: str, *, install_type: str = "") -> bool:
    if _is_simple_command(cmd):
        if shutil.which(cmd) is not None:
            return True
        # Only source nvm for npm-type tools — avoids unnecessary shell overhead
        prefix = f"{_npm_init()} && " if install_type == "npm" else ""
        try:
            return _probe(
                ["bash", "-lc", f"{prefix}command -v {cmd} >/dev/null 2>&1"],
                capture_output=True,
            ).returncode == 0
        except Exception:
            return False
    # Same nvm sourcing as the simple-command branch above. `bash -lc` reads ~/.profile,
    # whose sourcing of ~/.bashrc (where nvm's init lives) returns early when the shell
    # is non-interactive — so an nvm-installed binary is never on PATH here. Without
    # this, any npm-type tool with a multi-word check_cmd reports "not installed"
    # immediately after installing successfully.
    prefix = f"{_npm_init()} && " if install_type == "npm" else ""
    try:
        return _probe(["bash", "-lc", f"{prefix}{cmd}"], capture_output=True).returncode == 0
    except Exception:
        return False
