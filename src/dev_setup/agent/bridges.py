from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from dev_setup import registry as tool_registry
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.sandbox import SandboxError, Workspace


class InstallFailed(SandboxError):
    """An installer ran and failed. A subclass so the local agent handles it as it always did;
    the MCP server tells it apart from "unknown tool" to add the FR-13 hint (it cannot answer a
    password prompt, so the client has to be told where to run the install instead)."""


def _tool_line(tool) -> str:
    state = "installed" if tool.is_installed() else "available"
    return f"{tool.key}  [{state}]  {tool.description}"


def list_tools(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    tool_registry.init()
    tools = tool_registry.all_tools()
    if args.get("installed_only"):
        tools = [t for t in tools if t.is_installed()]
    if not tools:
        return "No tools matched."
    return "\n".join(_tool_line(t) for t in tools)


def search_catalog(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    tool_registry.init()
    query = str(args["query"]).lower()
    hits = [
        t
        for t in tool_registry.all_tools()
        if query in t.key.lower() or query in t.name.lower() or query in t.description.lower()
    ]
    if not hits:
        return f"No catalog tool matches '{args['query']}'."
    return "\n".join(_tool_line(t) for t in hits)


def tool_info(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    tool_registry.init()
    tool = tool_registry.get(args["key"])
    if tool is None:
        raise SandboxError(f"unknown catalog tool '{args['key']}'; try search_catalog first")

    lines = [
        f"key: {tool.key}",
        f"name: {tool.name}",
        f"description: {tool.description}",
        f"category: {tool.category}",
        f"install type: {tool.install_type}",
        f"installed: {'yes' if tool.is_installed() else 'no'}",
    ]
    if tool.requires:
        lines.append(f"requires: {', '.join(tool.requires)}")
    if tool.docs_url:
        lines.append(f"docs: {tool.docs_url}")
    return "\n".join(lines)


def _string_list(args: dict[str, Any], name: str) -> list[str]:
    """A list-of-strings argument. `bind()` checks names, not types, so this is the type check."""
    value = args.get(name, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        # A bare string must be refused, not iterated: "eslint" would read as six one-letter keys.
        raise SandboxError(f"'{name}' must be a list of strings, e.g. [\"uv\", \"nvm\"]")
    return value


def outdated(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    """`devstuff outdated --json`: the same rows, built by the same function (spec FR-9).

    `unknown` and `unsupported` rows are returned as such and never as `current` -- that honesty
    is the point of the command, and it is decided in `updates.classify`, not here.
    """
    from dev_setup.updates import State, build_rows

    updates_only = args.get("updates_only", False)
    if not isinstance(updates_only, bool):
        raise SandboxError("'updates_only' must be true or false")

    keys = _string_list(args, "packages")
    tool_registry.init()
    named = None
    if keys:
        named = []
        for key in keys:
            tool = tool_registry.get(key)
            if tool is None:
                raise SandboxError(f"unknown catalog tool '{key}'; try search_catalog first")
            named.append(tool)

    rows = build_rows(named)
    if updates_only:
        rows = [r for r in rows if r.state is State.OUTDATED]
    return json.dumps([r.to_json() for r in rows], indent=2)


def profile_diff(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    """`devstuff profile diff @name --json`, for a bundle that ships with devstuff (spec FR-10).

    Only a bundle name is accepted. A path would let a caller read any YAML file the user can read,
    and `bundles.resolve` matches the name against a directory listing rather than building a path
    from it, so there is nothing to traverse.
    """
    from dev_setup import bundles, profile, snapshot

    name = args["profile"]
    if not isinstance(name, str) or not bundles.is_bundle(name):
        raise SandboxError(
            f"'profile' must be a bundle name such as @devops, not a file path. "
            f"Bundles: {', '.join(f'@{n}' for n in bundles.names())}"
        )
    try:
        wanted = bundles.resolve(name)
    except profile.ProfileError as exc:
        raise SandboxError(str(exc)) from exc

    tool_registry.init()
    rows = profile.sort_diff_rows(profile.compare(wanted, snapshot.machine_facts(wanted)))
    return json.dumps([r.to_json() for r in rows], indent=2)


def install_tool(ws: Workspace, config: AgentConfig, args: dict[str, Any]) -> str:
    tool_registry.init()
    tool = tool_registry.get(args["key"])
    if tool is None:
        raise SandboxError(f"unknown catalog tool '{args['key']}'; try search_catalog first")

    if tool.is_installed():
        return f"{tool.name} is already installed."

    missing = tool_registry.missing_requires(tool)
    if missing:
        names = ", ".join(m.key if hasattr(m, "key") else str(m) for m in missing)
        raise SandboxError(f"{tool.key} requires these first: {names}")

    # Installs run with inherited stdio so an apt/sudo password prompt reaches the
    # user's terminal. Capturing it would hang the loop on an invisible prompt.
    try:
        version = tool.install()
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        raise InstallFailed(f"installing {tool.key} failed: {exc}") from exc

    return f"Installed {tool.name}" + (f" ({version})" if version else "")


def run_function(ws: Workspace, config: AgentConfig, target: str, args: dict[str, Any]) -> str:
    """Invoke a functions.yaml `script` function with named params."""
    from dev_setup import function_runner, functions_registry

    functions_registry.init()
    fn = functions_registry.get(target)
    if fn is None:
        raise SandboxError(f"unknown function '{target}'")

    # A full-width positional tuple, empty string for anything the model omitted.
    # function_runner maps values to params *by position*, so filtering absent ones
    # out would shift every later argument into the wrong variable. An empty string
    # is what resolve_params already treats as "not provided", so it falls through
    # to the catalog default or a missing-required error.
    ordered = tuple(str(args.get(p.name, "")) for p in fn.params)
    try:
        output = function_runner.run_script_function(fn, ordered, prompt=None, capture=True)
    except function_runner.ParamResolutionError as exc:
        raise SandboxError(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        # Hand the script's own diagnostics back, not just the exit code -- functions
        # guard on their dependencies with messages like "yq is required, install it
        # first", and dropping those leaves the model to invent a cause.
        detail = ((exc.stdout or "") + (exc.stderr or "")).strip()
        suffix = f": {detail}" if detail else ""
        raise SandboxError(f"{target} failed with exit code {exc.returncode}{suffix}") from exc
    return output or f"{fn.name} completed."


Bridge = Callable[[Workspace, AgentConfig, dict[str, Any]], str]

_BRIDGES: dict[str, Bridge] = {
    "list_tools": list_tools,
    "search_catalog": search_catalog,
    "tool_info": tool_info,
    "outdated": outdated,
    "profile_diff": profile_diff,
    "install_tool": install_tool,
}


def get(target: str) -> Bridge | None:
    return _BRIDGES.get(target)
