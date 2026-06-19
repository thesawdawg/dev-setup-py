from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from dev_setup.base import WhichTool, patch_bashrc, remove_bashrc_block

STARSHIP_BLOCK = "Starship prompt"
STARSHIP_INIT_LINE = 'eval "$(starship init bash)"'


class StarshipTool(WhichTool):
    key = "starship"
    name = "Starship"
    description = "Fast, cross-shell customizable prompt"
    category = "tools"
    install_type = "script"
    help_cmd = "starship --help"
    docs_url = "https://starship.rs/guide/"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        with ui.spinner("Installing Starship..."):
            subprocess.run(
                ["bash", "-c", "curl -fsSL https://starship.rs/install.sh | sh -s -- --yes"],
                check=True, capture_output=True,
            )

        if not self.is_installed():
            raise RuntimeError("Starship installation failed")

        added = patch_bashrc(STARSHIP_BLOCK, STARSHIP_INIT_LINE)
        if added:
            ui.info("Starship init added to ~/.bashrc")
        else:
            ui.dim("Starship init already in ~/.bashrc")

        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        for p in [
            Path("/usr/local/bin/starship"),
            Path.home() / ".cargo" / "bin" / "starship",
            Path.home() / ".local" / "bin" / "starship",
        ]:
            if p.exists():
                if str(p).startswith("/usr"):
                    subprocess.run(["sudo", "rm", "-f", str(p)], capture_output=True)
                else:
                    p.unlink()
                ui.dim(f"Removed: {p}")

        remove_bashrc_block(STARSHIP_BLOCK)
        ui.info("Starship init removed from ~/.bashrc")
