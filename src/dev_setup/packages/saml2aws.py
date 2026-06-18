from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

from dev_setup.base import Tool

_INSTALL_PATH = Path("/usr/local/bin/saml2aws")
_RELEASES_API = "https://api.github.com/repos/Versent/saml2aws/releases/latest"


class Saml2AwsTool(Tool):
    key = "saml2aws"
    name = "saml2aws"
    description = "SAML → AWS STS credentials CLI (Versent)"
    category = "tools"
    install_type = "script"

    def is_installed(self) -> bool:
        return shutil.which("saml2aws") is not None

    def get_version(self) -> str:
        r = subprocess.run(["saml2aws", "--version"], capture_output=True, text=True)
        out = r.stdout.strip() or r.stderr.strip()
        return out.splitlines()[0] if out else ""

    def install(self) -> Optional[str]:
        from dev_setup import ui

        version = self._latest_version()
        arch = "arm64" if platform.machine() == "aarch64" else "amd64"
        filename = f"saml2aws_{version}_linux_{arch}.tar.gz"
        url = (
            f"https://github.com/Versent/saml2aws/releases/download/"
            f"v{version}/{filename}"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / filename

            with ui.spinner(f"Downloading saml2aws v{version}..."):
                subprocess.run(
                    ["curl", "-fsSL", "-o", str(archive), url],
                    check=True, capture_output=True,
                )

            with ui.spinner("Extracting saml2aws..."):
                with tarfile.open(archive) as tf:
                    tf.extractall(tmpdir)

            binary = Path(tmpdir) / "saml2aws"
            if not binary.exists():
                raise RuntimeError(f"saml2aws binary not found in archive")

            with ui.spinner("Installing saml2aws to /usr/local/bin..."):
                subprocess.run(
                    ["sudo", "mv", str(binary), str(_INSTALL_PATH)],
                    check=True, capture_output=True,
                )
                subprocess.run(
                    ["sudo", "chmod", "+x", str(_INSTALL_PATH)],
                    check=True, capture_output=True,
                )

        if not self.is_installed():
            raise RuntimeError("saml2aws installation failed — binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        if not _INSTALL_PATH.exists():
            raise RuntimeError(f"saml2aws not found at {_INSTALL_PATH}")

        with ui.spinner("Removing saml2aws..."):
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
                tag = data["tag_name"]
                return tag.lstrip("v")
        except Exception:
            return "2.36.6"
