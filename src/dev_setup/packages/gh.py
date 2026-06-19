from __future__ import annotations

import subprocess
from typing import Optional

from dev_setup.base import WhichTool


class GhTool(WhichTool):
    key = "gh"
    name = "GitHub CLI"
    description = "GitHub's official CLI — PRs, issues, Actions, and more"
    category = "tools"
    install_type = "script"
    help_cmd = "gh --help"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        ui.info("Adding GitHub CLI apt repository...")
        subprocess.run(
            [
                "bash", "-c",
                "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg"
                " | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg"
                " && sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg"
                ' && echo "deb [arch=$(dpkg --print-architecture)'
                " signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg]"
                ' https://cli.github.com/packages stable main"'
                " | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null",
            ],
            check=True, capture_output=True,
        )

        with ui.spinner("Updating package index..."):
            subprocess.run(["sudo", "apt-get", "update", "-q"], check=True, capture_output=True)

        ui.info("Installing gh...")
        subprocess.run(["sudo", "apt-get", "install", "-y", "gh"], check=True, capture_output=True)

        if not self.is_installed():
            raise RuntimeError("gh installation failed — binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner("Removing gh..."):
            subprocess.run(
                ["sudo", "apt-get", "remove", "-y", "gh"],
                check=True, capture_output=True,
            )
