from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from dev_setup.base import WhichTool, patch_bashrc

UV_PATH_BLOCK = "uv (and other ~/.local/bin tools)"
UV_PATH_LINE = 'export PATH="$HOME/.local/bin:$PATH"'


class UvTool(WhichTool):
    key = "uv"
    name = "uv"
    description = "Astral Python package and project manager"
    category = "core"
    install_type = "script"
    help_cmd = "uv --help"
    docs_url = "https://docs.astral.sh/uv/"

    def is_installed(self) -> bool:
        return shutil.which("uv") is not None and shutil.which("uvx") is not None

    def install(self) -> Optional[str]:
        from dev_setup import ui

        with ui.spinner("Installing uv..."):
            subprocess.run(
                ["bash", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                check=True, capture_output=True,
            )

        import os
        path = os.environ.get("PATH", "")
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in path:
            os.environ["PATH"] = f"{local_bin}:{path}"

        if not shutil.which("uv") or not shutil.which("uvx"):
            raise RuntimeError("uv/uvx binaries not found after install — add ~/.local/bin to PATH")

        patch_bashrc(UV_PATH_BLOCK, UV_PATH_LINE)
        ui.info("~/.local/bin added to PATH in ~/.bashrc")

        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        removed = False
        for p in [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".local" / "bin" / "uvx",
            Path.home() / ".cargo" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uvx",
        ]:
            if p.exists():
                p.unlink()
                ui.dim(f"Removed: {p}")
                removed = True

        if not removed:
            raise RuntimeError("uv binary not found in ~/.local/bin or ~/.cargo/bin")
