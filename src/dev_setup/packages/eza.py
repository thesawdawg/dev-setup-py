from __future__ import annotations

import subprocess
from typing import Optional

from dev_setup.base import WhichTool


class EzaTool(WhichTool):
    key = "eza"
    name = "eza"
    description = "Modern ls replacement with git status, icons, and tree view"
    category = "tools"
    install_type = "script"
    help_cmd = "eza --help"
    docs_url = "https://eza.rocks/"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        ui.info("Adding eza apt repository...")
        subprocess.run(
            [
                "bash", "-c",
                "curl -fsSL https://raw.githubusercontent.com/eza-community/eza/main/deb.asc"
                " | sudo gpg --dearmor -o /etc/apt/keyrings/gierens.gpg"
                " && echo 'deb [signed-by=/etc/apt/keyrings/gierens.gpg]"
                " http://deb.gierens.de stable main'"
                " | sudo tee /etc/apt/sources.list.d/gierens.list > /dev/null"
                " && sudo chmod 644 /etc/apt/keyrings/gierens.gpg"
                " /etc/apt/sources.list.d/gierens.list",
            ],
            check=True, capture_output=True,
        )

        with ui.spinner("Updating package index..."):
            subprocess.run(["sudo", "apt-get", "update", "-q"], check=True, capture_output=True)

        ui.info("Installing eza...")
        subprocess.run(["sudo", "apt-get", "install", "-y", "eza"], check=True, capture_output=True)

        if not self.is_installed():
            raise RuntimeError("eza installation failed — binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner("Removing eza..."):
            subprocess.run(
                ["sudo", "apt-get", "remove", "-y", "eza"],
                check=True, capture_output=True,
            )
        subprocess.run(
            ["sudo", "rm", "-f",
             "/etc/apt/keyrings/gierens.gpg",
             "/etc/apt/sources.list.d/gierens.list"],
            check=True, capture_output=True,
        )
