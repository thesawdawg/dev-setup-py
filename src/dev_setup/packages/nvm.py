from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

from dev_setup.base import Tool, patch_bashrc, remove_bashrc_block

NVM_DIR = Path.home() / ".nvm"
NVM_INIT_BLOCK = "nvm"
NVM_INIT_LINES = (
    'export NVM_DIR="$HOME/.nvm"\n'
    '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"\n'
    '[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"'
)


class NvmTool(Tool):
    key = "nvm"
    name = "NVM + Node LTS"
    description = "Node Version Manager + latest Node LTS"
    category = "core"
    install_type = "script"

    def is_installed(self) -> bool:
        return (NVM_DIR / "nvm.sh").exists()

    def get_version(self) -> str:
        r = subprocess.run(
            ["bash", "-c", f'. "{NVM_DIR}/nvm.sh" && nvm --version'],
            capture_output=True, text=True,
        )
        return f"nvm v{r.stdout.strip()}" if r.returncode == 0 else ""

    def install(self) -> Optional[str]:
        from dev_setup import ui

        tag = self._latest_release_tag()
        url = f"https://raw.githubusercontent.com/nvm-sh/nvm/{tag}/install.sh"

        with ui.spinner(f"Installing NVM {tag}..."):
            subprocess.run(
                ["bash", "-c", f"curl -o- '{url}' | bash"],
                check=True, capture_output=True,
            )

        if not self.is_installed():
            raise RuntimeError("NVM installation failed — nvm.sh not found")

        patch_bashrc(NVM_INIT_BLOCK, NVM_INIT_LINES)
        ui.success("NVM init added to ~/.bashrc")

        with ui.spinner("Installing Node LTS..."):
            subprocess.run(
                ["bash", "-c", f'. "{NVM_DIR}/nvm.sh" && nvm install --lts'],
                check=True, capture_output=True,
            )

        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        if NVM_DIR.exists():
            import shutil as _shutil
            with ui.spinner("Removing NVM directory..."):
                _shutil.rmtree(NVM_DIR)

        remove_bashrc_block(NVM_INIT_BLOCK)
        ui.info("NVM init removed from ~/.bashrc")

    @staticmethod
    def _latest_release_tag() -> str:
        try:
            url = "https://api.github.com/repos/nvm-sh/nvm/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "dev-setup"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data["tag_name"]
        except Exception:
            return "v0.40.3"
