from __future__ import annotations

import re
import shlex
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


# -- command denylist ------------------------------------------------------------
#
# The confirmation prompt is a human attention filter, and human attention degrades
# over a long session. These rules are the part that does not get tired, so they are
# checked before any prompt is shown and are not disabled by --yolo.

_PRIVILEGED = {"sudo", "doas", "su", "pkexec"}
_DESTRUCTIVE = {
    "shutdown", "reboot", "poweroff", "halt", "init", "telinit",
    "mkfs", "fdisk", "parted", "sfdisk", "mkswap",
    "userdel", "usermod", "groupdel", "visudo", "passwd",
    "iptables", "nft", "systemctl", "service",
}
_RECURSIVE_RM = re.compile(r"^-[a-zA-Z]*[rR][a-zA-Z]*$")
_CATASTROPHIC_TARGETS = {"/", "/*", "~", "~/", "$HOME", "$HOME/", "/etc", "/usr", "/var", "/boot"}

_PIPE_TO_SHELL = re.compile(r"\|\s*(?:sudo\s+)?(?:ba|z|k|da)?sh\b")
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;?\s*:")
_DD_TO_DEVICE = re.compile(r"\bdd\b[^;&|]*\bof=/dev/(?!null\b|zero\b)")
_REDIRECT = re.compile(r"(?<![0-9<>])>>?\s*['\"]?([^\s;&|<>'\"]+)")
_SEPARATORS = re.compile(r"\|\||&&|[;|&\n]")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _unquoted(command: str) -> str:
    """Blank out quoted spans before applying whole-string pattern rules.

    Without this, `git commit -m 'pipe to sh'` reads as a pipe-to-shell. Redirect
    detection deliberately runs on the *original* string instead: missing a real
    `> /etc/passwd` matters more than refusing an echoed one.
    """
    return _QUOTED.sub(lambda m: " " * len(m.group(0)), command)


def _segments(command: str) -> list[list[str]]:
    """Split a command line into shell segments, each tokenised.

    A segment that will not tokenise (unbalanced quotes) yields an empty token
    list rather than raising -- an unparseable segment must not slip through the
    checks below, so callers treat it as "nothing recognised" and the whole-string
    regex rules still apply.
    """
    out = []
    for raw in _SEPARATORS.split(command):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(shlex.split(raw))
        except ValueError:
            out.append([])
    return out


def _binary(tokens: list[str]) -> str:
    """First token that is a command name rather than a VAR=value prefix."""
    for token in tokens:
        if "=" in token.split("/")[0] and not token.startswith(("-", "/", ".")):
            continue
        return Path(token).name
    return ""


def check_command(command: str, *, workspace: Workspace, extra_patterns: list[str] | None = None) -> None:
    """Refuse a command outright. Raises SandboxError with a reason the model can
    act on -- it is fed back as a tool error so the agent can re-plan."""
    if not command.strip():
        raise SandboxError("empty command")

    for pattern in extra_patterns or []:
        try:
            if re.search(pattern, command):
                raise SandboxError(f"command matches a denied pattern from agent.yaml: {pattern}")
        except re.error:
            continue  # a broken user pattern must not wedge every command

    bare = _unquoted(command)
    if _FORK_BOMB.search(bare):
        raise SandboxError("that looks like a fork bomb")
    if _PIPE_TO_SHELL.search(bare):
        raise SandboxError(
            "piping a download into a shell is not permitted; "
            "use install_tool, or fetch the script and read it first"
        )
    if _DD_TO_DEVICE.search(bare):
        raise SandboxError("writing directly to a block device is not permitted")

    for tokens in _segments(command):
        if not tokens:
            continue
        binary = _binary(tokens)

        if binary in _PRIVILEGED:
            raise SandboxError(
                f"'{binary}' is not permitted; "
                "use install_tool for catalog tools, which handles privilege escalation itself"
            )
        # `mkfs.ext4` and friends are the same hazard as `mkfs`.
        if binary in _DESTRUCTIVE or binary.split(".")[0] in _DESTRUCTIVE:
            raise SandboxError(f"'{binary}' can affect the whole system and is not permitted")

        if binary == "rm":
            args = [t for t in tokens[1:] if not t.startswith("-")]
            recursive = any(_RECURSIVE_RM.match(t) for t in tokens[1:] if t.startswith("-"))
            for arg in args:
                if arg in _CATASTROPHIC_TARGETS or (recursive and arg.rstrip("/") in ("", "/")):
                    raise SandboxError(f"refusing to delete {arg}")

        # Any token naming a protected location, wherever it appears in the argv.
        for token in tokens[1:]:
            _reject_protected(token)

    for match in _REDIRECT.finditer(command):
        target = match.group(1)
        _reject_protected(target)
        if target.startswith(("/", "~")):
            try:
                workspace.resolve(target, write=True)
            except SandboxError as exc:
                raise SandboxError(f"redirecting output outside the workspace: {exc}") from exc


def _reject_protected(token: str) -> None:
    """Block commands touching credential material or the devstuff catalogs.

    Deliberately narrower than full path containment for commands: requiring every
    absolute path in an argv to sit inside the workspace trips over ordinary flags
    (`-I/usr/include`, `PATH=/usr/local/bin`) and would push users to --yolo, which
    is strictly worse. Reads and writes still go through Workspace.resolve.
    """
    if not token.startswith(("/", "~")):
        return
    candidate = Path(token).expanduser()
    for blocked in _blocked_always() + _blocked_write():
        if _is_within(candidate, blocked) or candidate == blocked:
            raise SandboxError(f"commands may not touch {blocked}")


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
