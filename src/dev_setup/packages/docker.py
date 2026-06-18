from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from dev_setup.base import Tool


class DockerTool(Tool):
    key = "docker"
    name = "Docker"
    description = "Container runtime + docker compose plugin"
    category = "core"
    install_type = "script"
    help_cmd = "docker --help"

    def is_installed(self) -> bool:
        return shutil.which("docker") is not None

    def get_version(self) -> str:
        r = subprocess.run(["docker", "--version"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    def install(self) -> Optional[str]:
        from dev_setup import ui

        tmp = tempfile.NamedTemporaryFile(suffix=".sh", delete=False)
        tmp.close()
        try:
            with ui.spinner("Downloading Docker install script..."):
                subprocess.run(
                    ["curl", "-fsSL", "https://get.docker.com", "-o", tmp.name],
                    check=True, capture_output=True,
                )
            with ui.spinner("Installing Docker (this may take a minute)..."):
                subprocess.run(["sudo", "sh", tmp.name], check=True, capture_output=True)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

        user = os.environ.get("USER", "")
        if user:
            r = subprocess.run(["groups", user], capture_output=True, text=True)
            if "docker" not in r.stdout:
                ui.info(f"Adding {user} to docker group...")
                subprocess.run(
                    ["sudo", "usermod", "-aG", "docker", user],
                    check=True, capture_output=True,
                )
                ui.warn("Log out and back in for docker group to take effect")

        subprocess.run(["sudo", "systemctl", "enable", "docker"], capture_output=True)
        subprocess.run(["sudo", "systemctl", "start", "docker"], capture_output=True)
        self._ensure_compose(ui)

        if not self.is_installed():
            raise RuntimeError("Docker installation failed — docker binary not found")
        return self.get_version()

    def _ensure_compose(self, ui) -> None:  # type: ignore[no-untyped-def]
        r = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if r.returncode == 0:
            return
        ui.info("Installing docker-compose-plugin...")
        for mgr_cmd in [
            ["sudo", "apt-get", "install", "-y", "docker-compose-plugin"],
            ["sudo", "yum", "install", "-y", "docker-compose-plugin"],
            ["sudo", "dnf", "install", "-y", "docker-compose-plugin"],
        ]:
            r = subprocess.run(mgr_cmd, capture_output=True)
            if r.returncode == 0:
                return
        ui.warn("docker-compose-plugin not available via package manager")

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner("Stopping Docker service..."):
            subprocess.run(["sudo", "systemctl", "stop", "docker"], capture_output=True)
            subprocess.run(["sudo", "systemctl", "disable", "docker"], capture_output=True)

        with ui.spinner("Removing Docker packages..."):
            subprocess.run(
                ["bash", "-c",
                 "sudo apt-get remove -y docker-ce docker-ce-cli containerd.io "
                 "docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras "
                 "2>/dev/null || sudo yum remove -y docker-ce docker-ce-cli containerd.io "
                 "docker-compose-plugin 2>/dev/null || true"],
                capture_output=True,
            )
