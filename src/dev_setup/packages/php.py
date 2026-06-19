from __future__ import annotations

import shutil
import subprocess
from typing import Optional

from dev_setup.base import WhichTool


class PhpTool(WhichTool):
    key = "php"
    name = "PHP 8.4"
    description = "PHP 8.4 + common extensions via ondrej/php PPA"
    category = "tools"
    install_type = "apt"
    help_cmd = "php --help"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        if not shutil.which("add-apt-repository"):
            with ui.spinner("Installing software-properties-common..."):
                subprocess.run(
                    ["sudo", "apt-get", "install", "-y", "software-properties-common"],
                    check=True, capture_output=True,
                )

        with ui.spinner("Adding ondrej/php PPA..."):
            subprocess.run(
                ["sudo", "add-apt-repository", "-y", "ppa:ondrej/php"],
                check=True, capture_output=True,
            )

        with ui.spinner("Updating package index..."):
            subprocess.run(["sudo", "apt-get", "update", "-q"], check=True, capture_output=True)

        packages = [
            "php8.4", "php8.4-cli", "php8.4-common",
            "php8.4-curl", "php8.4-mbstring", "php8.4-xml", "php8.4-zip",
        ]
        with ui.spinner("Installing PHP 8.4 + extensions..."):
            subprocess.run(
                ["sudo", "apt-get", "install", "-y"] + packages,
                check=True, capture_output=True,
            )

        if not self.is_installed():
            raise RuntimeError("PHP installation failed")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner("Removing PHP 8.4 packages..."):
            subprocess.run(
                ["bash", "-c", "sudo apt-get remove -y 'php8.4*' && sudo apt-get autoremove -y"],
                check=True, capture_output=True,
            )
