from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class Tool(ABC):
    key: str = ""
    name: str = ""
    description: str = ""
    category: str = "custom"
    install_type: str = "unknown"
    builtin: bool = True
    help_cmd: str = ""
    docs_url: str = ""

    @abstractmethod
    def is_installed(self) -> bool: ...

    @abstractmethod
    def install(self) -> Optional[str]:
        """Install the tool. Return version string if available. Raise on failure."""
        ...

    @abstractmethod
    def remove(self) -> None:
        """Uninstall the tool. Raise on failure."""
        ...

    def get_version(self) -> str:
        return ""


class WhichTool(Tool):
    """Base for tools whose presence is detected via PATH lookup (shutil.which)."""

    def is_installed(self) -> bool:
        return shutil.which(self.key) is not None

    def get_version(self) -> str:
        if not shutil.which(self.key):
            return ""
        r = subprocess.run([self.key, "--version"], capture_output=True, text=True)
        if r.returncode != 0:
            return ""
        out = r.stdout.strip() or r.stderr.strip()
        return out.splitlines()[0] if out else ""


def run_bash(cmd: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], **kwargs)


def patch_bashrc(block_name: str, content: str) -> bool:
    """Idempotently append a named block to ~/.bashrc. Returns True if added."""
    bashrc = Path.home() / ".bashrc"
    marker = f"# {block_name}"

    if bashrc.exists() and marker in bashrc.read_text():
        return False

    with bashrc.open("a") as f:
        f.write(f"\n{marker}\n{content}\n")
    return True


def remove_bashrc_block(block_name: str) -> bool:
    """Remove a named block (and the line after the marker) from ~/.bashrc."""
    bashrc = Path.home() / ".bashrc"
    if not bashrc.exists():
        return False

    lines = bashrc.read_text().splitlines(keepends=True)
    marker = f"# {block_name}"
    out = []
    i = 0
    removed = False
    while i < len(lines):
        stripped = lines[i].rstrip()
        if stripped == marker:
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            if i < len(lines) and not lines[i].strip():
                i += 1
            removed = True
        else:
            out.append(lines[i])
            i += 1

    if removed:
        bashrc.write_text("".join(out))
    return removed
