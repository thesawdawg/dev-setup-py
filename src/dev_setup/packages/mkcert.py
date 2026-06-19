from __future__ import annotations

import json
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

from dev_setup.base import WhichTool

_INSTALL_PATH = Path("/usr/local/bin/mkcert")
_RELEASES_API = "https://api.github.com/repos/FiloSottile/mkcert/releases/latest"


class MkcertTool(WhichTool):
    key = "mkcert"
    name = "mkcert"
    description = "Zero-config local HTTPS certificates"
    category = "tools"
    install_type = "script"
    help_cmd = "mkcert --help"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        version = self._latest_version()
        arch = "arm64" if platform.machine() == "aarch64" else "amd64"
        url = (
            f"https://github.com/FiloSottile/mkcert/releases/download/"
            f"v{version}/mkcert-v{version}-linux-{arch}"
        )

        with ui.spinner(f"Downloading mkcert v{version}..."):
            subprocess.run(
                ["sudo", "curl", "-fsSL", "-o", str(_INSTALL_PATH), url],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["sudo", "chmod", "+x", str(_INSTALL_PATH)],
                check=True, capture_output=True,
            )

        if not self.is_installed():
            raise RuntimeError("mkcert installation failed — binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        if not _INSTALL_PATH.exists():
            raise RuntimeError(f"mkcert not found at {_INSTALL_PATH}")

        with ui.spinner("Removing mkcert..."):
            subprocess.run(
                ["sudo", "rm", "-f", str(_INSTALL_PATH)],
                check=True, capture_output=True,
            )

    @staticmethod
    def _latest_version() -> str:
        try:
            req = urllib.request.Request(
                _RELEASES_API, headers={"User-Agent": "dev-setup"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data["tag_name"].lstrip("v")
        except Exception:
            return "1.4.4"
