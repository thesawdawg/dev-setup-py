from __future__ import annotations

import errno
import ipaddress
import re
import sys
from pathlib import Path

import click

from dev_setup.catalog import CatalogError
from dev_setup.mcp import http, process, stdio, token
from dev_setup.mcp.core import Dispatcher, Toolbox

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765  # fixed on purpose: a configured client's URL must survive a restart (SD-8)

_HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")
_ORIGIN = re.compile(r"https?://[^/\s]+")


def _warn(message: str) -> None:
    click.echo(f"devstuff mcp: {message}", err=True)


def _fail(message: str) -> None:
    click.echo(f"error: {message}", err=True)
    sys.exit(1)


def _toolbox(allow_install: bool, allow_functions: bool) -> Toolbox:
    from dev_setup.agent import registry as agent_registry

    return Toolbox.from_agent(
        agent_registry.build(), allow_install=allow_install, allow_functions=allow_functions, warn=_warn
    )


def _enabled(allow_install: bool, allow_functions: bool) -> str:
    gated = [
        label
        for label, on in (("install_tool", allow_install), ("functions", allow_functions))
        if on
    ]
    return f"+ {', '.join(gated)}" if gated else "read-only; install_tool and functions are off"


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@click.command("mcp")
@click.option(
    "--http", "use_http", is_flag=True,
    help="Serve over HTTP instead of stdio: MCP at POST /mcp, REST at /tools, the OpenAPI document at "
    "/openapi.json. Every request needs the bearer token.",
)
@click.option(
    "--host", default=None, metavar="ADDR",
    help=f"[--http] Address to listen on (default {DEFAULT_HOST}, this machine only). A non-loopback "
    "address is allowed, and warned about: there is no TLS.",
)
@click.option(
    "--port", type=click.IntRange(0, 65535), default=None, metavar="N",
    help=f"[--http] Port (default {DEFAULT_PORT}). 0 asks the OS. A busy port is an error, never a fallback.",
)
@click.option(
    "--token-file", type=click.Path(dir_okay=False, path_type=Path), default=None, metavar="PATH",
    help="[--http] Where the bearer token lives (default ~/.local/state/devstuff/mcp-token). Created "
    "on first use, mode 0600, reused on every run. Its path is printed; its contents never are.",
)
@click.option("--rotate-token", is_flag=True, help="[--http] Replace the token with a new one.")
@click.option(
    "--allow-origin", "allow_origins", multiple=True, metavar="ORIGIN",
    help="[--http] Accept requests carrying this Origin (repeatable). Default: none, so a browser "
    "is refused.",
)
@click.option(
    "--allow-host", "allow_hosts", multiple=True, metavar="NAME",
    help="[--http] Accept this name in the Host header (repeatable), e.g. host.docker.internal. "
    "`localhost` and IP addresses are always accepted.",
)
@click.option(
    "--allow-install", is_flag=True,
    help="Expose install_tool, which installs a catalog tool as you. Off by default: absent and refused.",
)
@click.option(
    "--allow-functions", is_flag=True,
    help="Expose every `type: script` function (fn_<key>). Off by default: absent and refused.",
)
@click.option(
    "--list-tools", "list_tools", is_flag=True,
    help="Print the tools these flags would expose, and which gate each sits behind. Starts nothing.",
)
def mcp_cmd(
    use_http: bool,
    host: str | None,
    port: int | None,
    token_file: Path | None,
    rotate_token: bool,
    allow_origins: tuple[str, ...],
    allow_hosts: tuple[str, ...],
    allow_install: bool,
    allow_functions: bool,
    list_tools: bool,
) -> None:
    """Serve devstuff's tools to an MCP client (stdio) or over HTTP, MCP and OpenAPI.

    By default it speaks MCP over stdio: it reads JSON-RPC from stdin and answers on stdout, and is
    meant to be started by a client (Claude Code, Claude Desktop), not by hand. With --http it
    listens on a port instead.

    Only read-only tools are exposed unless a flag says otherwise, and a tool that is off does not
    merely hide: a call naming it is refused.

    The server has no terminal. An installer that needs a password fails at once rather than
    waiting for one nobody can type; run `devstuff install <key>` in a terminal for those.
    """
    http_only = {
        "--host": host is not None, "--port": port is not None, "--token-file": token_file is not None,
        "--rotate-token": rotate_token, "--allow-origin": bool(allow_origins),
        "--allow-host": bool(allow_hosts),
    }
    if not use_http and any(http_only.values()):
        given = ", ".join(flag for flag, on in http_only.items() if on)
        raise click.UsageError(f"{given} only apply with --http.")
    for name in allow_hosts:
        if not _HOSTNAME.fullmatch(name):
            raise click.UsageError(
                f"--allow-host takes a bare host name (no port, scheme or path), not {name!r}."
            )
    for origin in allow_origins:
        if not _ORIGIN.fullmatch(origin):
            raise click.UsageError(
                f"--allow-origin takes an origin like http://localhost:3000, not {origin!r}."
            )

    if list_tools:
        _print_tools(_load(allow_install, allow_functions))
        return

    # Detach first, before the fd move and before anything that could run an installer (FR-11a).
    # Under a shell this forks and the original process becomes a supervisor that never returns.
    process.detach()
    try:
        process.require_no_terminal(gated=allow_install or allow_functions, warn=_warn)
    except process.TerminalReachable as exc:
        _fail(str(exc))

    box = _load(allow_install, allow_functions)
    if not use_http:
        what = _enabled(allow_install, allow_functions)
        click.echo(f"devstuff mcp: serving {len(box.names())} tools on stdio ({what})", err=True)
        sys.exit(stdio.run(Dispatcher(box)))
    _serve_http(
        box, Dispatcher(box), host or DEFAULT_HOST, DEFAULT_PORT if port is None else port,
        token_file, rotate_token, allow_origins, allow_hosts, _enabled(allow_install, allow_functions),
    )


def _serve_http(
    box: Toolbox, dispatcher: Dispatcher, host: str, port: int, token_file: Path | None,
    rotate: bool, allow_origins: tuple[str, ...], allow_hosts: tuple[str, ...], enabled: str,
) -> None:
    try:
        secret, path = token.load(token_file, rotate=rotate)
    except token.TokenError as exc:
        _fail(str(exc))
    try:
        server = http.make_server(
            dispatcher, host=host, port=port, token=secret,
            allow_hosts=allow_hosts, allow_origins=allow_origins,
        )
    except OSError as exc:
        hint = ""
        if exc.errno == errno.EADDRINUSE:
            hint = (
                " Another process is using it. devstuff never picks a different port: a client "
                "configured for this one would fail with no explanation. Stop that process, or pass --port."
            )
        _fail(f"cannot listen on {host}:{port}: {exc.strerror or exc}.{hint}")

    # With the port held and before any request can arrive: nothing a tool prints may reach the
    # terminal or read from it (FR-11).
    stdio.isolate_stdio(channel=False)
    bound = server.bound_port
    shown = f"[{host}]" if ":" in host else host
    click.echo(
        f"devstuff mcp: serving {len(box.names())} tools on http://{shown}:{bound} ({enabled}); "
        f"bearer token in {path}",
        err=True,
    )
    if not _is_loopback(host):
        _warn(
            f"listening on {host}, not just this machine. There is no TLS: the token crosses the network in "
            f"the clear, and anyone who holds it can use every enabled tool ({enabled}). To reach a "
            "loopback server from another machine, an SSH tunnel is safer."
        )
    sys.exit(http.serve(server))


def _load(allow_install: bool, allow_functions: bool) -> Toolbox:
    try:
        return _toolbox(allow_install, allow_functions)
    except CatalogError as exc:
        _fail(str(exc))
        raise  # unreachable: _fail exits


def _print_tools(box: Toolbox) -> None:
    width = max((len(n) for n in box.names()), default=0)
    for name in box.names():
        tool = box.get(name)
        first = (tool.description.splitlines() or [""])[0]
        click.echo(f"{name:<{width}}  [{box.gate(name).value}]  {first}")
