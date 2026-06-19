from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from dev_setup.base import Tool, patch_bashrc, remove_bashrc_block

_RBENV_DIR = Path.home() / ".rbenv"
_RBENV_BIN = _RBENV_DIR / "bin" / "rbenv"
_RUBY_BUILD_DIR = _RBENV_DIR / "plugins" / "ruby-build"

_RBENV_BLOCK = "rbenv"
_RBENV_INIT_LINES = (
    'export PATH="$HOME/.rbenv/bin:$PATH"\n'
    'eval "$(rbenv init - bash)"'
)

_BUILD_DEPS = [
    "git", "curl", "autoconf", "bison", "build-essential",
    "libssl-dev", "libyaml-dev", "libreadline-dev",
    "zlib1g-dev", "libncurses5-dev", "libffi-dev", "libgdbm-dev",
]


class RubyTool(Tool):
    key = "ruby"
    name = "Ruby (rbenv)"
    description = "Ruby via rbenv version manager + ruby-build"
    category = "languages"
    install_type = "script"
    help_cmd = "ruby --version"

    def is_installed(self) -> bool:
        return _RBENV_BIN.exists()

    def get_version(self) -> str:
        if not _RBENV_BIN.exists():
            return ""
        r = subprocess.run(
            ["bash", "-c", f'eval "$(~/.rbenv/bin/rbenv init - bash)" && ruby --version'],
            capture_output=True, text=True,
        )
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 else ""

    def install(self) -> Optional[str]:
        from dev_setup import ui

        ui.info("Installing Ruby build dependencies...")
        subprocess.run(
            ["sudo", "apt-get", "install", "-y"] + _BUILD_DEPS,
            check=True, capture_output=True,
        )

        with ui.spinner("Cloning rbenv..."):
            subprocess.run(
                ["git", "clone", "--depth=1", "https://github.com/rbenv/rbenv.git", str(_RBENV_DIR)],
                check=True, capture_output=True,
            )

        with ui.spinner("Cloning ruby-build plugin..."):
            subprocess.run(
                ["git", "clone", "--depth=1", "https://github.com/rbenv/ruby-build.git",
                 str(_RUBY_BUILD_DIR)],
                check=True, capture_output=True,
            )

        patch_bashrc(_RBENV_BLOCK, _RBENV_INIT_LINES)
        ui.info("rbenv init added to ~/.bashrc")

        latest = self._latest_stable()
        ui.info(f"Installing Ruby {latest} (this may take several minutes)...")
        subprocess.run(
            [str(_RBENV_BIN), "install", latest],
            check=True,
        )
        subprocess.run(
            [str(_RBENV_BIN), "global", latest],
            check=True, capture_output=True,
        )

        if not self.is_installed():
            raise RuntimeError("Ruby installation failed — rbenv not found")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui

        if not _RBENV_DIR.exists():
            raise RuntimeError(f"rbenv not found at {_RBENV_DIR}")

        import shutil
        with ui.spinner("Removing rbenv and all Ruby versions..."):
            shutil.rmtree(_RBENV_DIR)

        remove_bashrc_block(_RBENV_BLOCK)
        ui.info("rbenv init removed from ~/.bashrc")

    def _latest_stable(self) -> str:
        try:
            r = subprocess.run(
                [str(_RBENV_BIN), "install", "-l"],
                capture_output=True, text=True, check=True,
            )
            versions = [
                line.strip() for line in r.stdout.splitlines()
                if line.strip() and line.strip()[0].isdigit() and "-" not in line
            ]
            if versions:
                return versions[-1]
        except Exception:
            pass
        return "3.3.6"
