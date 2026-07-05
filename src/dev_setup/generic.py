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

from dev_setup.base import Tool

_verbose: bool = False

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
# fields never read from / written to the catalog
_NON_CATALOG = ("key", "builtin")


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
    """Run a command. Streams output when verbose, captures when not."""
    if _verbose:
        subprocess.run(cmd, check=True, cwd=cwd)
    else:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=cwd)
        except subprocess.CalledProcessError as e:
            msg = e.stderr.strip() if e.stderr else f"exit code {e.returncode}"
            raise RuntimeError(msg) from e


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
    git_url: str = ""
    git_install_cmd: str = ""
    git_remove_cmd: str = ""
    apt_packages: str = ""
    script_url: str = ""
    sha256: str = ""
    install_script: str = ""
    remove_script: str = ""
    help_cmd: str = ""
    docs_url: str = ""
    requires: list | None = None
    builtin: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.key
        if self.requires is None:
            self.requires = list(AUTO_REQUIRES.get(self.install_type, []))

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

    # -- Strategy dispatch ----------------------------------------------------

    def is_installed(self) -> bool:
        if self.check_cmd:
            return _check_cmd_installed(self.check_cmd, install_type=self.install_type)
        checker = _CHECKERS.get(self.install_type)
        return checker(self) if checker else False

    def install(self) -> str | None:
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

    def update(self, version: str | None = None) -> str | None:
        """Update an already-installed tool to the latest (or a specified) version."""
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
                    r = subprocess.run([cmd, flag], capture_output=True, text=True, timeout=5)
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
    from dev_setup import ui
    if not tool.npm_name:
        raise RuntimeError("npm_name not set")
    with ui.spinner(f"Installing {tool.name} via npm..."):
        subprocess.run(
            ["bash", "-lc", f"{_npm_init()} && npm install -g {shlex.quote(tool.npm_name)}"],
            check=True, capture_output=True,
        )


def _install_uvx(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.pip_name:
        raise RuntimeError("pip_name not set")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required to install uvx packages. "
            "Install it first: dev-setup install uv"
        )
    with ui.spinner(f"Installing {tool.name} via uvx..."):
        subprocess.run([uv, "tool", "install", tool.pip_name], check=True, capture_output=True)


def _install_git(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.git_url:
        raise RuntimeError("git_url not set")
    dest = _git_clone_dest(tool.git_url)
    with ui.spinner(f"Cloning {tool.name}..."):
        subprocess.run(
            ["git", "clone", "--depth=1", tool.git_url, str(dest)],
            check=True, capture_output=True,
        )
    if tool.git_install_cmd:
        with ui.spinner("Running install command..."):
            subprocess.run(
                ["bash", "-c", tool.git_install_cmd],
                cwd=dest, check=True, capture_output=True,
            )


def _install_apt(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.apt_packages:
        raise RuntimeError("apt_packages not set")
    ui.info(f"Installing {tool.name} via apt...")
    subprocess.run(["sudo", "apt-get", "update", "-q"], capture_output=True)
    _run(["sudo", "apt-get", "install", "-y"] + tool.apt_packages.split())


def _install_script_url(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.script_url:
        raise RuntimeError("script_url not set")
    ui.info(f"Running install script for {tool.name}...")
    script = _download_script(tool.script_url, expected_sha256=tool.sha256)
    _run_bash_script(script)


def _install_bash(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.install_script:
        raise RuntimeError("install_script not set")
    ui.info(f"Installing {tool.name}...")
    _run_bash_script(tool.install_script)


# -- Update strategies --------------------------------------------------------


def _update_npm(tool: GenericTool, version: str | None) -> None:
    from dev_setup import ui
    if not tool.npm_name:
        raise RuntimeError("npm_name not set")
    target = f"{tool.npm_name}@{version or 'latest'}"
    with ui.spinner(f"Updating {tool.name} via npm..."):
        subprocess.run(
            ["bash", "-lc", f"{_npm_init()} && npm install -g {shlex.quote(target)}"],
            check=True, capture_output=True,
        )


def _update_uvx(tool: GenericTool, version: str | None) -> None:
    from dev_setup import ui
    if not tool.pip_name:
        raise RuntimeError("pip_name not set")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required to update uvx packages. "
            "Install it first: dev-setup install uv"
        )
    target = f"{tool.pip_name}=={version}" if version else tool.pip_name
    with ui.spinner(f"Updating {tool.name} via uv tool upgrade..."):
        subprocess.run([uv, "tool", "upgrade", target], check=True, capture_output=True)


def _update_apt(tool: GenericTool, version: str | None) -> None:
    from dev_setup import ui
    if not tool.apt_packages:
        raise RuntimeError("apt_packages not set")
    packages = tool.apt_packages.split()
    subprocess.run(["sudo", "apt-get", "update", "-q"], capture_output=True)
    if version:
        if len(packages) != 1:
            raise RuntimeError(
                "Version pinning is only supported for apt tools with a single package."
            )
        ui.info(f"Updating {tool.name} to version {version} via apt...")
        _run(["sudo", "apt-get", "install", "-y", f"{packages[0]}={version}"])
    else:
        ui.info(f"Updating {tool.name} via apt...")
        _run(["sudo", "apt-get", "install", "--only-upgrade", "-y"] + packages)


def _update_git(tool: GenericTool, version: str | None) -> None:
    from dev_setup import ui
    if version:
        raise RuntimeError(
            "Version pinning is not supported for git tools (shallow clone). "
            f"Reinstall instead: dev-setup remove {tool.key} && dev-setup install {tool.key}"
        )
    if not tool.git_url:
        raise RuntimeError("git_url not set")
    dest = _git_clone_dest(tool.git_url)
    if not dest.exists():
        raise RuntimeError(f"{tool.name} clone not found at {dest}")
    with ui.spinner(f"Pulling latest {tool.name}..."):
        _run(["git", "-C", str(dest), "pull"])
        if tool.git_install_cmd:
            subprocess.run(
                ["bash", "-c", tool.git_install_cmd],
                cwd=dest, check=True, capture_output=True,
            )


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
        r = subprocess.run(
            ["bash", "-lc", f"{_npm_init()} && npm list -g --depth=0 --json {shlex.quote(pkg)}"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout or "{}")
        return data.get("dependencies", {}).get(pkg, {}).get("version", "")
    except Exception:
        return ""


def _npm_latest_version(pkg: str) -> str:
    try:
        r = subprocess.run(
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
        r = subprocess.run(
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
        r = subprocess.run(
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


def _check_update_apt(tool: GenericTool) -> UpdateStatus:
    if not tool.apt_packages:
        return UpdateStatus()
    pkg = tool.apt_packages.split()[0]
    try:
        r = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", pkg], capture_output=True, text=True, timeout=10,
        )
        current = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        current = ""
    try:
        r = subprocess.run(["apt-cache", "policy", pkg], capture_output=True, text=True, timeout=10)
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
        r = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        current = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        current = ""
    try:
        r = subprocess.run(
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
    "apt": _check_update_apt,
    "git": _check_update_git,
}


# -- Remove strategies -----------------------------------------------------------


def _remove_npm(tool: GenericTool) -> None:
    from dev_setup import ui
    with ui.spinner(f"Removing {tool.name}..."):
        subprocess.run(
            ["bash", "-lc", f"{_npm_init()} && npm uninstall -g {shlex.quote(tool.npm_name)}"],
            check=True, capture_output=True,
        )


def _remove_uvx(tool: GenericTool) -> None:
    from dev_setup import ui
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "uv is required to remove uvx packages. "
            "Install it first: dev-setup install uv"
        )
    with ui.spinner(f"Removing {tool.name}..."):
        subprocess.run([uv, "tool", "uninstall", tool.pip_name], check=True, capture_output=True)


def _remove_git(tool: GenericTool) -> None:
    from dev_setup import ui
    dest = _git_clone_dest(tool.git_url)
    if tool.git_remove_cmd:
        with ui.spinner("Running remove command..."):
            subprocess.run(["bash", "-c", tool.git_remove_cmd], cwd=dest, capture_output=True)
    if dest.exists():
        shutil.rmtree(dest)


def _remove_apt(tool: GenericTool) -> None:
    from dev_setup import ui
    ui.info(f"Removing {tool.name}...")
    if tool.remove_script:
        _run_bash_script(tool.remove_script)
    else:
        _run(["sudo", "apt-get", "remove", "-y"] + tool.apt_packages.split())


def _remove_script_url(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.remove_script:
        raise RuntimeError(
            "No remove script defined for this script-installed package. "
            "Remove manually then run: dev-setup delete " + tool.key
        )
    ui.info(f"Removing {tool.name}...")
    _run_bash_script(tool.remove_script)


def _remove_bash(tool: GenericTool) -> None:
    from dev_setup import ui
    if not tool.remove_script:
        raise RuntimeError(
            f"No remove script defined for '{tool.key}'. "
            "Remove manually then run: dev-setup delete " + tool.key
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


def _installed_apt(tool: GenericTool) -> bool:
    return bool(tool.apt_packages) and _apt_installed(tool.apt_packages.split()[0])


_INSTALLERS: dict[str, Callable[[GenericTool], None]] = {
    "npm": _install_npm,
    "pip": _install_uvx,
    "uvx": _install_uvx,
    "git": _install_git,
    "apt": _install_apt,
    "script": _install_script_url,
    "bash": _install_bash,
}

_REMOVERS: dict[str, Callable[[GenericTool], None]] = {
    "npm": _remove_npm,
    "pip": _remove_uvx,
    "uvx": _remove_uvx,
    "git": _remove_git,
    "apt": _remove_apt,
    "script": _remove_script_url,
    "bash": _remove_bash,
}

_CHECKERS: dict[str, Callable[[GenericTool], bool]] = {
    "npm": _installed_npm,
    "pip": _installed_uvx,
    "uvx": _installed_uvx,
    "git": _installed_git,
    "apt": _installed_apt,
}

_UPDATERS: dict[str, Callable[[GenericTool, str | None], None]] = {
    "npm": _update_npm,
    "pip": _update_uvx,
    "uvx": _update_uvx,
    "git": _update_git,
    "apt": _update_apt,
    "script": _update_script_url,
    "bash": _update_bash,
}


# -- Helpers ----------------------------------------------------------------------


def _npm_global_installed(pkg: str) -> bool:
    try:
        r = subprocess.run(
            ["bash", "-lc", f"{_npm_init()} && npm list -g --depth=0 {shlex.quote(pkg)}"],
            capture_output=True,
            text=True,
        )
        return pkg in r.stdout
    except Exception:
        return False


def _npm_init() -> str:
    return '. "$HOME/.nvm/nvm.sh" 2>/dev/null || true'


def _apt_installed(pkg: str) -> bool:
    try:
        r = subprocess.run(["dpkg", "-s", pkg], capture_output=True, text=True)
        return "Status: install ok installed" in r.stdout
    except Exception:
        return False


def _git_clone_dest(url: str) -> Path:
    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    return Path.home() / ".local" / "share" / "dev-setup" / repo_name


def _download_script(url: str, *, expected_sha256: str = "") -> str:
    """Download a script over HTTPS and optionally verify its sha256."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read()

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

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(script)
        tmp = f.name
    try:
        _run(["bash", tmp])
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
            r = subprocess.run(
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
            return subprocess.run(
                ["bash", "-lc", f"{prefix}command -v {cmd} >/dev/null 2>&1"],
                capture_output=True,
            ).returncode == 0
        except Exception:
            return False
    try:
        return subprocess.run(["bash", "-lc", cmd], capture_output=True).returncode == 0
    except Exception:
        return False
