from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from dev_setup.agent import sandbox
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.sandbox import SandboxError, Workspace

# Anything above this and the model is almost certainly about to blow its own
# context on one file. Reported plainly so it can narrow the request instead.
_MAX_READ_BYTES = 200_000


def read_file(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    path = ws.resolve(args["path"])
    if not path.exists():
        raise SandboxError(f"no such file: {args['path']}")
    if path.is_dir():
        raise SandboxError(f"{args['path']} is a directory; use list_dir")
    if path.stat().st_size > _MAX_READ_BYTES:
        raise SandboxError(f"{args['path']} is too large to read ({path.stat().st_size} bytes)")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SandboxError(f"{args['path']} is not a UTF-8 text file") from exc


def write_file(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    path = ws.resolve(args["path"], write=True)
    if path.is_dir():
        raise SandboxError(f"{args['path']} is a directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = args["content"]
    existed = path.exists()
    path.write_text(content, encoding="utf-8")
    verb = "Updated" if existed else "Created"
    return f"{verb} {ws.display(path)} ({len(content.splitlines())} lines)"


def list_dir(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    path = ws.resolve(args.get("path") or ".")
    if not path.exists():
        raise SandboxError(f"no such directory: {args.get('path') or '.'}")
    if not path.is_dir():
        raise SandboxError(f"{args.get('path')} is not a directory")

    entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    if not entries:
        return f"{ws.display(path)} is empty"
    lines = [f"{e.name}/" if e.is_dir() else e.name for e in entries]
    return f"{ws.display(path)}:\n" + "\n".join(lines)


def cd(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    target = ws.chdir(args["path"])
    return f"Working directory is now {ws.display(target)}"


def run_command(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    command = args["command"]
    sandbox.check_command(command, workspace=ws, extra_patterns=config.deny_patterns)

    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=ws.cwd,
            capture_output=True,
            text=True,
            timeout=config.command_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"command timed out after {config.command_timeout}s") from exc
    except OSError as exc:
        raise SandboxError(f"could not run command: {exc}") from exc

    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.strip()

    if proc.returncode != 0:
        # Returned as content rather than raised: a non-zero exit is information
        # the model needs in order to fix its own command, not a sandbox refusal.
        return f"exit code {proc.returncode}\n{output}" if output else f"exit code {proc.returncode}"
    return output or "(no output)"


Primitive = Callable[[Workspace, AgentConfig, dict[str, Any]], str]

_PRIMITIVES: dict[str, Primitive] = {
    "read_file": read_file,
    "write_file": write_file,
    "list_dir": list_dir,
    "cd": cd,
    "run_command": run_command,
}


def get(key: str) -> Primitive | None:
    return _PRIMITIVES.get(key)
