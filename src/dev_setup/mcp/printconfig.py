"""`devstuff mcp --print-config TARGET`: a snippet to paste into a client (docs/specs/mcp, FR-31, F-16).

Pure: it formats what it is given and touches no file, socket or process. The one secret on the page,
the bearer token, is a *path* in every snippet — the client's own shell reads the file when the command
runs — so a snippet is safe to paste into a ticket or a chat.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

TARGETS = ("claude-code", "claude-desktop", "openapi")
NAME = "devstuff"

_WILDCARDS = ("", "0.0.0.0", "::")  # "every address": a place to listen, not a place to connect


class ConfigError(ValueError):
    """A snippet that cannot be printed honestly for these flags."""


@dataclass(frozen=True)
class Spec:
    launcher: tuple[str, ...]
    http: bool
    host: str
    port: int
    token_file: Path
    allow_install: bool
    allow_functions: bool


@dataclass(frozen=True)
class Rendered:
    text: str
    notes: tuple[str, ...] = ()


def launcher(argv0: str, executable: str) -> tuple[str, ...]:
    """How to start the devstuff that is running now, as absolute words.

    A GUI application usually does not inherit the shell's `PATH`, so a bare `devstuff` would work in
    a terminal and fail in Claude Desktop. A console script keeps the path it was started by (a
    symlink such as `~/.local/bin/devstuff` is stable; the venv it points into is not). Anything else
    — `python -m dev_setup`, the `./devstuff` wrapper — is this interpreter running the module, which
    is exactly the code that is answering.
    """
    if argv0:
        candidate = Path(argv0)
        if candidate.name == "devstuff":
            absolute = Path(os.path.abspath(candidate))
            if absolute.is_file() and os.access(absolute, os.X_OK):
                return (str(absolute),)
    return (os.path.abspath(executable), "-m", "dev_setup")


def _gate_flags(spec: Spec) -> list[str]:
    flags = []
    if spec.allow_install:
        flags.append("--allow-install")
    if spec.allow_functions:
        flags.append("--allow-functions")
    return flags


def _connect_host(spec: Spec) -> tuple[str, tuple[str, ...]]:
    if spec.host in _WILDCARDS:
        shown = spec.host or "every address"
        return "127.0.0.1", (
            f"the server would be listening on every address ({shown}); the URL below uses 127.0.0.1, "
            "which reaches it from this machine. From another machine, use this machine's own name "
            "or address.",
        )
    return (f"[{spec.host}]" if ":" in spec.host else spec.host), ()


def _base_url(spec: Spec) -> tuple[str, tuple[str, ...]]:
    if spec.port == 0:
        raise ConfigError(
            "--port 0 asks the OS for a port, so there is no port to write down yet. Pick a port "
            "(the default is fine) and pass it to both the server and --print-config."
        )
    host, notes = _connect_host(spec)
    return f"http://{host}:{spec.port}", notes


def _server_command(spec: Spec) -> str:
    """The line that starts the server these snippets talk to, for the note on stderr."""
    words = [*spec.launcher, "mcp", "--http"]
    if spec.host:
        words += ["--host", spec.host]
    words += ["--port", str(spec.port)]
    if spec.token_file:
        words += ["--token-file", str(spec.token_file)]
    words += _gate_flags(spec)
    return shlex.join(words)


def _claude_code(spec: Spec) -> Rendered:
    if not spec.http:
        words = ["claude", "mcp", "add", NAME, "--", *spec.launcher, "mcp", *_gate_flags(spec)]
        return Rendered(shlex.join(words) + "\n")
    base, notes = _base_url(spec)
    # `$(cat …)` is expanded by the shell that runs this command, so the token is read when it is
    # pasted and never appears in what is printed. Quoting restarts inside `$( )`.
    header = f'"Authorization: Bearer $(cat {shlex.quote(str(spec.token_file))})"'
    text = f"claude mcp add --transport http {NAME} {base}/mcp --header {header}\n"
    return Rendered(text, (*notes, f"this talks to a server you start yourself: {_server_command(spec)}"))


def _claude_desktop(spec: Spec) -> Rendered:
    if spec.http:
        raise ConfigError(
            "Claude Desktop launches a command and talks to it over stdio, so there is no HTTP form to "
            "print. Leave out --http."
        )
    command, *args = spec.launcher
    entry = {"command": command, "args": [*args, "mcp", *_gate_flags(spec)]}
    return Rendered(json.dumps({"mcpServers": {NAME: entry}}, indent=2) + "\n")


def _openapi(spec: Spec) -> Rendered:
    if not spec.http:
        raise ConfigError("the OpenAPI document is served by the HTTP server: add --http.")
    base, notes = _base_url(spec)
    text = (
        f"Base URL:       {base}\n"
        f"OpenAPI URL:    {base}/openapi.json\n"
        f"Header:         Authorization: Bearer <the contents of {spec.token_file}>\n"
    )
    return Rendered(text, (*notes, f"this talks to a server you start yourself: {_server_command(spec)}"))


_RENDERERS = {"claude-code": _claude_code, "claude-desktop": _claude_desktop, "openapi": _openapi}


def render(target: str, spec: Spec) -> Rendered:
    try:
        renderer = _RENDERERS[target]
    except KeyError:
        raise ConfigError(f"unknown target {target!r}; choose one of: {', '.join(TARGETS)}.") from None
    return renderer(spec)
