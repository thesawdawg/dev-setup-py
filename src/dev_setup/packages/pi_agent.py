from __future__ import annotations

import subprocess
from typing import Optional

from dev_setup.base import Tool

_NPM_PACKAGE = "pi-coding-agent"


class PiAgentTool(Tool):
    key = "pi"
    name = "Pi Coding Agent"
    description = "AI coding agent — pi-coding-agent npm package"
    category = "tools"
    install_type = "npm"
    help_cmd = "pi --help"
    docs_url = "https://www.npmjs.com/package/pi-coding-agent"
    requires = ["nvm"]

    def is_installed(self) -> bool:
        try:
            r = subprocess.run(
                ["npm", "list", "-g", "--depth=0", _NPM_PACKAGE],
                capture_output=True, text=True,
            )
            return _NPM_PACKAGE in r.stdout
        except Exception:
            return False

    def get_version(self) -> str:
        try:
            r = subprocess.run(
                ["npm", "list", "-g", "--depth=0", "--json", _NPM_PACKAGE],
                capture_output=True, text=True,
            )
            import json
            data = json.loads(r.stdout)
            return data.get("dependencies", {}).get(_NPM_PACKAGE, {}).get("version", "")
        except Exception:
            return ""

    def install(self) -> Optional[str]:
        from dev_setup import ui

        with ui.spinner(f"Installing {self.name} via npm..."):
            subprocess.run(
                ["npm", "install", "-g", _NPM_PACKAGE],
                check=True, capture_output=True,
            )

        if not self.is_installed():
            raise RuntimeError(f"{self.name} installation failed")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner(f"Removing {self.name}..."):
            subprocess.run(
                ["npm", "uninstall", "-g", _NPM_PACKAGE],
                check=True, capture_output=True,
            )
