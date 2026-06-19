from __future__ import annotations

import subprocess
from typing import Optional

from dev_setup.base import WhichTool


class HtopTool(WhichTool):
    key = "htop"
    name = "htop"
    description = "Interactive process and resource monitor"
    category = "tools"
    install_type = "apt"
    help_cmd = "man htop"
    docs_url = "https://htop.dev/"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        with ui.spinner("Installing htop..."):
            result = subprocess.run(
                ["bash", "-c",
                 "sudo apt-get install -y htop 2>/dev/null || "
                 "sudo yum install -y htop 2>/dev/null || "
                 "sudo dnf install -y htop 2>/dev/null || "
                 "sudo pacman -S --noconfirm htop 2>/dev/null"],
                capture_output=True,
            )

        if not self.is_installed():
            raise RuntimeError("htop installation failed — no supported package manager found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner("Removing htop..."):
            subprocess.run(
                ["bash", "-c",
                 "sudo apt-get remove -y htop 2>/dev/null || "
                 "sudo yum remove -y htop 2>/dev/null || "
                 "sudo dnf remove -y htop 2>/dev/null || "
                 "sudo pacman -R --noconfirm htop 2>/dev/null"],
                capture_output=True,
            )
