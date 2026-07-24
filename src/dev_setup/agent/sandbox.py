from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dev_setup.catalog import CONFIG_DIR


class SandboxError(RuntimeError):
    """A path or command was refused. The message is returned to the model as a
    tool error, so it must say plainly what was refused and why."""


def _home() -> Path:
    return Path.home().resolve()


def _blocked_always() -> list[Path]:
    """Credential material: denied for read *and* write, regardless of where the
    workspace root sits. Reading an SSH key is as bad as overwriting one, and a
    root of $HOME would otherwise put these in bounds."""
    home = _home()
    return [home / ".ssh", home / ".aws", home / ".gnupg", home / ".config" / "gh"]


def _blocked_write() -> list[Path]:
    """Readable but never writable. The devstuff catalogs are the agent's own
    configuration -- it may consult them, but catalog authoring stays a human
    action (FR-14a), and write access here would be a back door to it."""
    return [CONFIG_DIR.resolve()]


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


@dataclass
class Workspace:
    """The agent's filesystem boundary. `root` is fixed for the session; `cwd`
    moves within it."""

    root: Path
    cwd: Path

    @classmethod
    def create(cls, root: str | Path) -> Workspace:
        resolved = Path(root).expanduser().resolve()
        if not resolved.exists():
            raise SandboxError(f"Workspace does not exist: {resolved}")
        if not resolved.is_dir():
            raise SandboxError(f"Workspace is not a directory: {resolved}")
        return cls(root=resolved, cwd=resolved)

    def resolve(self, path: str | Path, *, write: bool = False) -> Path:
        """Resolve a model-supplied path against the current directory and refuse
        anything outside the workspace or inside a protected location.

        `Path.resolve()` collapses `..` and follows symlinks *before* the
        containment check, so neither traversal nor a symlink planted inside the
        workspace can escape it.
        """
        candidate = Path(path).expanduser()
        candidate = candidate if candidate.is_absolute() else self.cwd / candidate
        resolved = candidate.resolve()

        if not _is_within(resolved, self.root):
            raise SandboxError(f"path escapes the workspace root {self.root}: {path}")

        for blocked in _blocked_always():
            if _is_within(resolved, blocked):
                raise SandboxError(f"access to {blocked} is not permitted: {path}")

        if write:
            for blocked in _blocked_write():
                if _is_within(resolved, blocked):
                    raise SandboxError(
                        f"writing to {blocked} is not permitted -- "
                        f"devstuff catalogs are edited by hand: {path}"
                    )

        return resolved

    def chdir(self, path: str | Path) -> Path:
        target = self.resolve(path)
        if not target.is_dir():
            raise SandboxError(f"not a directory: {path}")
        self.cwd = target
        return target

    def display(self, path: Path) -> str:
        """Workspace-relative rendering, for prompts and tool output."""
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return str(path)
        return f"./{rel}" if str(rel) != "." else "."


def _git(root: Path, *args: str) -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def assess(root: Path) -> list[str]:
    """Warnings about handing the agent this directory. Empty means unremarkable.

    Advisory only -- this informs the human at launch. It is not a security
    control; `Workspace.resolve` is.
    """
    warnings: list[str] = []
    root = root.resolve()
    home = _home()

    if root == Path("/") or root in (Path("/etc"), Path("/usr"), Path("/var")):
        warnings.append(f"{root} is a system directory — the agent could modify your OS.")
    elif root == home:
        warnings.append(
            "This is your entire home directory — the agent could reach every project in it."
        )
    elif _is_within(home, root):
        warnings.append(f"{root} contains your home directory.")

    toplevel = _git(root, "rev-parse", "--show-toplevel")
    if toplevel:
        status = _git(root, "status", "--porcelain")
        if status:
            count = len(status.splitlines())
            warnings.append(
                f"Git repo with {count} uncommitted change{'s' if count != 1 else ''} — "
                "the agent's edits would mix into work you haven't committed."
            )

    return warnings
