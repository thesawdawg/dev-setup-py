from __future__ import annotations

import sys

import click

from dev_setup.catalog import CatalogError
from dev_setup.mcp import process, stdio
from dev_setup.mcp.core import Dispatcher, Toolbox


def _warn(message: str) -> None:
    click.echo(f"devstuff mcp: {message}", err=True)


def _toolbox(allow_install: bool, allow_functions: bool) -> Toolbox:
    from dev_setup.agent import registry as agent_registry

    return Toolbox.from_agent(
        agent_registry.build(), allow_install=allow_install, allow_functions=allow_functions, warn=_warn
    )


def _banner(box: Toolbox, allow_install: bool, allow_functions: bool) -> str:
    gated = [
        label
        for label, on in (("install_tool", allow_install), ("functions", allow_functions))
        if on
    ]
    return (
        f"devstuff mcp: serving {len(box.names())} tools on stdio "
        f"({'+ ' + ', '.join(gated) if gated else 'read-only; install_tool and functions are off'})"
    )


@click.command("mcp")
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
def mcp_cmd(allow_install: bool, allow_functions: bool, list_tools: bool) -> None:
    """Serve devstuff's tools to an MCP client over stdio.

    Reads JSON-RPC from stdin and answers on stdout — it is meant to be started by a client
    (Claude Code, Claude Desktop), not by hand. Only read-only tools are exposed unless a flag
    says otherwise, and a tool that is off does not merely hide: a call naming it is refused.

    The server has no terminal. An installer that needs a password fails at once rather than
    waiting for one nobody can type; run `devstuff install <key>` in a terminal for those.
    """
    if list_tools:
        _print_tools(_load(allow_install, allow_functions))
        return

    # Detach first, before the fd move and before anything that could run an installer (FR-11a).
    # Under a shell this forks and the original process becomes a supervisor that never returns.
    process.detach()
    try:
        process.require_no_terminal(gated=allow_install or allow_functions, warn=_warn)
    except process.TerminalReachable as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    box = _load(allow_install, allow_functions)
    click.echo(_banner(box, allow_install, allow_functions), err=True)
    sys.exit(stdio.run(Dispatcher(box)))


def _load(allow_install: bool, allow_functions: bool) -> Toolbox:
    try:
        return _toolbox(allow_install, allow_functions)
    except CatalogError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


def _print_tools(box: Toolbox) -> None:
    width = max((len(n) for n in box.names()), default=0)
    for name in box.names():
        tool = box.get(name)
        first = (tool.description.splitlines() or [""])[0]
        click.echo(f"{name:<{width}}  [{box.gate(name).value}]  {first}")
