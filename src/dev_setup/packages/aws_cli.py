from __future__ import annotations

import platform
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from dev_setup.base import WhichTool

_AWS_CLI_DIR = Path("/usr/local/aws-cli")
_AWS_BIN = Path("/usr/local/bin/aws")


class AwsCliTool(WhichTool):
    key = "aws"
    name = "AWS CLI"
    description = "Amazon Web Services command line interface (v2)"
    category = "tools"
    install_type = "script"
    help_cmd = "aws help"

    def install(self) -> Optional[str]:
        from dev_setup import ui

        arch = "aarch64" if platform.machine() == "aarch64" else "x86_64"
        url = f"https://awscli.amazonaws.com/awscli-exe-linux-{arch}.zip"

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "awscliv2.zip"

            with ui.spinner("Downloading AWS CLI v2..."):
                subprocess.run(
                    ["curl", "-fsSL", url, "-o", str(zip_path)],
                    check=True, capture_output=True,
                )

            with ui.spinner("Extracting AWS CLI..."):
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(tmpdir)

            with ui.spinner("Installing AWS CLI (sudo)..."):
                subprocess.run(
                    ["sudo", str(Path(tmpdir) / "aws" / "install")],
                    check=True, capture_output=True,
                )

        if not self.is_installed():
            raise RuntimeError("AWS CLI installation failed — aws binary not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        with ui.spinner("Removing AWS CLI..."):
            for path in [
                _AWS_CLI_DIR,
                _AWS_BIN,
                Path("/usr/local/bin/aws_completer"),
            ]:
                if path.is_dir():
                    subprocess.run(["sudo", "rm", "-rf", str(path)], check=True, capture_output=True)
                elif path.exists():
                    subprocess.run(["sudo", "rm", "-f", str(path)], check=True, capture_output=True)
