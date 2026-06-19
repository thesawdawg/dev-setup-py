from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from dev_setup.base import WhichTool

_BINARY = Path("/usr/local/bin/ollama")


class OllamaTool(WhichTool):
    key = "ollama"
    name = "Ollama"
    description = "Run large language models locally"
    category = "tools"
    install_type = "script"
    help_cmd = "ollama --help"
    docs_url = "https://ollama.com/docs"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        ui.info("Installing Ollama...")
        subprocess.run(
            ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            check=True,
        )

        if not self.is_installed():
            raise RuntimeError("Ollama installation failed — binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner("Stopping Ollama service..."):
            subprocess.run(["sudo", "systemctl", "stop", "ollama"], capture_output=True)
            subprocess.run(["sudo", "systemctl", "disable", "ollama"], capture_output=True)

        with ui.spinner("Removing Ollama..."):
            for path in [
                _BINARY,
                Path("/etc/systemd/system/ollama.service"),
            ]:
                if path.exists():
                    subprocess.run(
                        ["sudo", "rm", "-f", str(path)],
                        check=True, capture_output=True,
                    )
            subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True)

        ui.dim("Note: downloaded models in ~/.ollama are left intact.")
