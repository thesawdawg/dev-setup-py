from __future__ import annotations

import subprocess
from typing import Optional

from dev_setup.base import WhichTool


class JavaTool(WhichTool):
    key = "java"
    name = "Java 21 (OpenJDK)"
    description = "OpenJDK 21 LTS — JDK and JRE"
    category = "languages"
    install_type = "apt"
    help_cmd = "java --help"
    docs_url = "https://openjdk.org/"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        with ui.spinner("Updating package index..."):
            subprocess.run(["sudo", "apt-get", "update", "-q"], check=True, capture_output=True)

        ui.info("Installing OpenJDK 21...")
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "openjdk-21-jdk"],
            check=True, capture_output=True,
        )

        if not self.is_installed():
            raise RuntimeError("Java installation failed — java binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        ui.info("Removing OpenJDK 21...")
        subprocess.run(
            ["sudo", "apt-get", "remove", "-y", "openjdk-21-jdk", "openjdk-21-jre"],
            check=True, capture_output=True,
        )
        subprocess.run(["sudo", "apt-get", "autoremove", "-y"], capture_output=True)
