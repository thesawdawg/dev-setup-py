from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from dev_setup.base import Tool

CUSTOM_DIR = Path.home() / ".config" / "dev-setup" / "packages"
_CUSTOM_DIR = CUSTOM_DIR


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
    ) -> None:
        self.key = key
        self.name = name
        self.description = description
        self.category = category
        self.install_type = install_type
        self.check_cmd = check_cmd
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

    @classmethod
    def from_dict(cls, data: dict, key: str) -> "GenericTool":
        return cls(
            key=key,
            name=data.get("name", key),
            description=data.get("description", ""),
            category=data.get("category", "custom"),
            install_type=data.get("type", "unknown"),
            check_cmd=data.get("check_cmd", ""),
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
        ]:
            if val:
                d[field] = val
        return d

    def save(self) -> None:
        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        path = CUSTOM_DIR / f"{self.key}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    def is_installed(self) -> bool:
        if self.check_cmd:
            return shutil.which(self.check_cmd) is not None
        t = self.install_type
        if t == "npm":
            return self.npm_name and _npm_global_installed(self.npm_name)
        if t == "pip":
            return bool(self.pip_name) and shutil.which(self.pip_name) is not None
        if t == "git":
            return bool(self.git_url) and _git_clone_dest(self.git_url).exists()
        if t == "apt":
            return bool(self.apt_packages) and _apt_installed(self.apt_packages.split()[0])
        return False

    def get_version(self) -> str:
        cmd = self.check_cmd or _type_cmd(self)
        if not cmd or not shutil.which(cmd):
            return ""
        for flag in ["--version", "-v", "version"]:
            try:
                r = subprocess.run([cmd, flag], capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip().splitlines()[0]
            except Exception:
                pass
        return "installed"

    def install(self) -> Optional[str]:
        from dev_setup import ui
        t = self.install_type

        if t == "npm":
            if not self.npm_name:
                raise RuntimeError("npm_name not set")
            with ui.spinner(f"Installing {self.name} via npm..."):
                subprocess.run(
                    ["npm", "install", "-g", self.npm_name],
                    check=True, capture_output=True,
                )
        elif t == "pip":
            if not self.pip_name:
                raise RuntimeError("pip_name not set")
            uv = shutil.which("uv")
            with ui.spinner(f"Installing {self.name} via pip..."):
                if uv:
                    subprocess.run(
                        [uv, "tool", "install", self.pip_name],
                        check=True, capture_output=True,
                    )
                else:
                    subprocess.run(
                        ["pip3", "install", "--user", self.pip_name],
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
            with ui.spinner(f"Installing {self.name} via apt..."):
                subprocess.run(
                    ["sudo", "apt-get", "install", "-y"] + self.apt_packages.split(),
                    check=True, capture_output=True,
                )
        elif t == "script":
            if not self.script_url:
                raise RuntimeError("script_url not set")
            with ui.spinner(f"Running install script for {self.name}..."):
                subprocess.run(
                    ["bash", "-c", f"curl -fsSL '{self.script_url}' | sh"],
                    check=True, capture_output=True,
                )
        elif t == "bash":
            if not self.install_script:
                raise RuntimeError("install_script not set")
            with ui.spinner(f"Installing {self.name}..."):
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
                    ["npm", "uninstall", "-g", self.npm_name],
                    check=True, capture_output=True,
                )
        elif t == "pip":
            uv = shutil.which("uv")
            with ui.spinner(f"Removing {self.name}..."):
                if uv:
                    subprocess.run(
                        [uv, "tool", "uninstall", self.pip_name],
                        check=True, capture_output=True,
                    )
                else:
                    subprocess.run(
                        ["pip3", "uninstall", "-y", self.pip_name],
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
            import shutil as _shutil
            if dest.exists():
                _shutil.rmtree(dest)
        elif t == "apt":
            with ui.spinner(f"Removing {self.name}..."):
                subprocess.run(
                    ["sudo", "apt-get", "remove", "-y"] + self.apt_packages.split(),
                    check=True, capture_output=True,
                )
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
            with ui.spinner(f"Removing {self.name}..."):
                _run_bash_script(self.remove_script)
        else:
            raise RuntimeError(f"Unsupported remove type: {t!r}")


def _npm_global_installed(pkg: str) -> bool:
    try:
        r = subprocess.run(["npm", "list", "-g", "--depth=0", pkg], capture_output=True, text=True)
        return pkg in r.stdout
    except Exception:
        return False


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
    """Write script to a temp file and execute it with bash, capturing output."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(script)
        tmp = f.name
    try:
        result = subprocess.run(["bash", tmp], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or f"Script exited with code {result.returncode}"
            )
    finally:
        os.unlink(tmp)


def _type_cmd(tool: GenericTool) -> str:
    t = tool.install_type
    if t == "npm":
        return tool.npm_name
    if t == "pip":
        return tool.pip_name
    return ""
