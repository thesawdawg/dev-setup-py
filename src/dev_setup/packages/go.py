from __future__ import annotations

import json
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

from dev_setup.base import WhichTool, patch_bashrc, remove_bashrc_block

_GO_DIR = Path("/usr/local/go")
_GO_PATH_BLOCK = "Go"
_GO_PATH_LINE = 'export PATH=$PATH:/usr/local/go/bin'
_RELEASES_API = "https://go.dev/dl/?mode=json"


class GoTool(WhichTool):
    key = "go"
    name = "Go"
    description = "Go programming language toolchain"
    category = "languages"
    install_type = "script"
    help_cmd = "go help"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        version = self._latest_version()
        arch = "arm64" if platform.machine() == "aarch64" else "amd64"
        filename = f"go{version}.linux-{arch}.tar.gz"
        url = f"https://go.dev/dl/{filename}"

        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / filename

            with ui.spinner(f"Downloading Go {version}..."):
                subprocess.run(
                    ["curl", "-fsSL", "-o", str(archive), url],
                    check=True, capture_output=True,
                )

            if _GO_DIR.exists():
                with ui.spinner("Removing previous Go installation..."):
                    subprocess.run(
                        ["sudo", "rm", "-rf", str(_GO_DIR)],
                        check=True, capture_output=True,
                    )

            with ui.spinner("Extracting Go..."):
                with tarfile.open(archive) as tf:
                    members = [m for m in tf.getmembers() if not _is_unsafe_path(m.name)]
                    with tempfile.TemporaryDirectory() as extract_dir:
                        tf.extractall(extract_dir, members=members)
                        subprocess.run(
                            ["sudo", "mv", str(Path(extract_dir) / "go"), str(_GO_DIR)],
                            check=True, capture_output=True,
                        )

        added = patch_bashrc(_GO_PATH_BLOCK, _GO_PATH_LINE)
        if added:
            ui.info("Go bin added to PATH in ~/.bashrc")

        import os
        path = os.environ.get("PATH", "")
        go_bin = str(_GO_DIR / "bin")
        if go_bin not in path:
            os.environ["PATH"] = f"{path}:{go_bin}"

        if not self.is_installed():
            raise RuntimeError("Go installation failed — go binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        if not _GO_DIR.exists():
            raise RuntimeError(f"Go not found at {_GO_DIR}")

        with ui.spinner("Removing Go..."):
            subprocess.run(
                ["sudo", "rm", "-rf", str(_GO_DIR)],
                check=True, capture_output=True,
            )

        remove_bashrc_block(_GO_PATH_BLOCK)
        ui.info("Go PATH entry removed from ~/.bashrc")

    @staticmethod
    def _latest_version() -> str:
        try:
            req = urllib.request.Request(_RELEASES_API, headers={"User-Agent": "dev-setup"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                releases = json.loads(resp.read())
                for r in releases:
                    if r.get("stable"):
                        return r["version"].lstrip("go")
        except Exception:
            pass
        return "1.23.4"


def _is_unsafe_path(name: str) -> bool:
    return name.startswith("/") or ".." in name
