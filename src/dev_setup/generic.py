from __future__ import annotations

import shutil
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from dev_setup.base import Tool

_verbose: bool = False


def _run(cmd: list, *, cwd: Optional[Path] = None) -> None:
    """Run a command. Streams output when verbose, captures when not."""
    if _verbose:
        subprocess.run(cmd, check=True, cwd=cwd)
    else:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=cwd)
        except subprocess.CalledProcessError as e:
            msg = e.stderr.strip() if e.stderr else f"exit code {e.returncode}"
            raise RuntimeError(msg) from e


class GenericTool(Tool):
    category = "custom"
    builtin = False

    def __init__(
        self,
        key: str,
        name: str,
        description: str = "",
        category: str = "custom",
        install_type: str = "unknown",
        check_cmd: str = "",
        version_cmd: str = "",
        npm_name: str = "",
        pip_name: str = "",
        git_url: str = "",
        git_install_cmd: str = "",
        git_remove_cmd: str = "",
        apt_packages: str = "",
        script_url: str = "",
        install_script: str = "",
        remove_script: str = "",
        help_cmd: str = "",
        docs_url: str = "",
        requires: Optional[list] = None,
    ) -> None:
        self.key = key
        self.name = name
        self.description = description
        self.category = category
        self.install_type = install_type
        self.check_cmd = check_cmd
        self.version_cmd = version_cmd
        self.npm_name = npm_name
        self.pip_name = pip_name
        self.git_url = git_url
        self.git_install_cmd = git_install_cmd
        self.git_remove_cmd = git_remove_cmd
        self.apt_packages = apt_packages
        self.script_url = script_url
        self.install_script = install_script
        self.remove_script = remove_script
        self.help_cmd = help_cmd
        self.docs_url = docs_url
        # npm packages need nvm/node; explicit catalog requires takes precedence
        if requires is not None:
            self.requires = requires
        elif install_type == "npm":
            self.requires = ["nvm"]
        elif install_type in ("pip", "uvx"):
            self.requires = ["uv"]
        else:
            self.requires = []

    @classmethod
    def from_dict(cls, data: dict, key: str) -> "GenericTool":
        return cls(
            key=key,
            name=data.get("name", key),
            description=data.get("description", ""),
            category=data.get("category", "custom"),
            install_type=data.get("type", "unknown"),
            check_cmd=data.get("check_cmd", ""),
            version_cmd=data.get("version_cmd", ""),
            npm_name=data.get("npm_name", ""),
            pip_name=data.get("pip_name", ""),
            git_url=data.get("git_url", ""),
            git_install_cmd=data.get("git_install_cmd", ""),
            git_remove_cmd=data.get("git_remove_cmd", ""),
            apt_packages=data.get("apt_packages", ""),
            script_url=data.get("script_url", ""),
            install_script=data.get("install_script", ""),
            remove_script=data.get("remove_script", ""),
            help_cmd=data.get("help_cmd", ""),
            docs_url=data.get("docs_url", ""),
            requires=data.get("requires"),
        )

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "type": self.install_type,
        }
        for field, val in [
            ("check_cmd", self.check_cmd),
            ("version_cmd", self.version_cmd),
            ("npm_name", self.npm_name),
            ("pip_name", self.pip_name),
            ("git_url", self.git_url),
            ("git_install_cmd", self.git_install_cmd),
            ("git_remove_cmd", self.git_remove_cmd),
            ("apt_packages", self.apt_packages),
            ("script_url", self.script_url),
            ("install_script", self.install_script),
            ("remove_script", self.remove_script),
            ("help_cmd", self.help_cmd),
            ("docs_url", self.docs_url),
        ]:
            if val:
                d[field] = val
        # Only persist explicit requires — auto-inferred ones are re-derived on load
        auto = (
            (self.install_type == "npm" and self.requires == ["nvm"])
            or (self.install_type in ("pip", "uvx") and self.requires == ["uv"])
        )
        if self.requires is not None and not auto:
            d["requires"] = self.requires
        return d

    def save(self) -> None:
        from dev_setup import catalog
        catalog.save_user_tool(self.key, self.to_dict())

    def is_installed(self) -> bool:
        if self.check_cmd:
            return _check_cmd_installed(self.check_cmd, install_type=self.install_type)
        t = self.install_type
        if t == "npm":
            return self.npm_name and _npm_global_installed(self.npm_name)
        if t in ("pip", "uvx"):
            return bool(self.pip_name) and shutil.which(self.pip_name) is not None
        if t == "git":
            return bool(self.git_url) and _git_clone_dest(self.git_url).exists()
        if t == "apt":
            return bool(self.apt_packages) and _apt_installed(self.apt_packages.split()[0])
        return False

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

    def install(self) -> Optional[str]:
        from dev_setup import ui
        t = self.install_type

        if t == "npm":
            if not self.npm_name:
                raise RuntimeError("npm_name not set")
            with ui.spinner(f"Installing {self.name} via npm..."):
                subprocess.run(
                    ["bash", "-lc", f"{_npm_init()} && npm install -g {shlex.quote(self.npm_name)}"],
                    check=True, capture_output=True,
                )
        elif t in ("pip", "uvx"):
            if not self.pip_name:
                raise RuntimeError("pip_name not set")
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError(
                    "uv is required to install uvx packages. "
                    "Install it first: dev-setup install uv"
                )
            with ui.spinner(f"Installing {self.name} via uvx..."):
                subprocess.run(
                    [uv, "tool", "install", self.pip_name],
                    check=True, capture_output=True,
                )
        elif t == "git":
            if not self.git_url:
                raise RuntimeError("git_url not set")
            dest = _git_clone_dest(self.git_url)
            with ui.spinner(f"Cloning {self.name}..."):
                subprocess.run(
                    ["git", "clone", "--depth=1", self.git_url, str(dest)],
                    check=True, capture_output=True,
                )
            if self.git_install_cmd:
                with ui.spinner(f"Running install command..."):
                    subprocess.run(
                        ["bash", "-c", self.git_install_cmd],
                        cwd=dest, check=True, capture_output=True,
                    )
        elif t == "apt":
            if not self.apt_packages:
                raise RuntimeError("apt_packages not set")
            ui.info(f"Installing {self.name} via apt...")
            subprocess.run(["sudo", "apt-get", "update", "-q"], capture_output=True)
            _run(["sudo", "apt-get", "install", "-y"] + self.apt_packages.split())
        elif t == "script":
            if not self.script_url:
                raise RuntimeError("script_url not set")
            ui.info(f"Running install script for {self.name}...")
            _run(["bash", "-c", f"curl -fsSL '{self.script_url}' | sh"])
        elif t == "bash":
            if not self.install_script:
                raise RuntimeError("install_script not set")
            ui.info(f"Installing {self.name}...")
            _run_bash_script(self.install_script)
        else:
            raise RuntimeError(f"Unsupported install type: {t!r}")

        return self.get_version() or None

    def remove(self) -> None:
        from dev_setup import ui
        t = self.install_type

        if t == "npm":
            with ui.spinner(f"Removing {self.name}..."):
                subprocess.run(
                    ["bash", "-lc", f"{_npm_init()} && npm uninstall -g {shlex.quote(self.npm_name)}"],
                    check=True, capture_output=True,
                )
        elif t in ("pip", "uvx"):
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError(
                    "uv is required to remove uvx packages. "
                    "Install it first: dev-setup install uv"
                )
            with ui.spinner(f"Removing {self.name}..."):
                subprocess.run(
                    [uv, "tool", "uninstall", self.pip_name],
                    check=True, capture_output=True,
                )
        elif t == "git":
            dest = _git_clone_dest(self.git_url)
            if self.git_remove_cmd:
                with ui.spinner(f"Running remove command..."):
                    subprocess.run(
                        ["bash", "-c", self.git_remove_cmd],
                        cwd=dest, capture_output=True,
                    )
            if dest.exists():
                shutil.rmtree(dest)
        elif t == "apt":
            ui.info(f"Removing {self.name}...")
            if self.remove_script:
                _run_bash_script(self.remove_script)
            else:
                _run(["sudo", "apt-get", "remove", "-y"] + self.apt_packages.split())
        elif t == "script":
            raise RuntimeError(
                "Script-type packages cannot be auto-removed. "
                "Remove manually then run: dev-setup delete " + self.key
            )
        elif t == "bash":
            if not self.remove_script:
                raise RuntimeError(
                    f"No remove script defined for '{self.key}'. "
                    "Remove manually then run: dev-setup delete " + self.key
                )
            ui.info(f"Removing {self.name}...")
            _run_bash_script(self.remove_script)
        else:
            raise RuntimeError(f"Unsupported remove type: {t!r}")


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
                ["bash", "-lc", f". \"$HOME/.nvm/nvm.sh\" 2>/dev/null; {shlex.quote(key)} {flag} 2>/dev/null"],
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
