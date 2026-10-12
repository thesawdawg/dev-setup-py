"""What the MCP server runs, what it will let a client reach, and how it answers (docs/specs/mcp).

No sockets, no processes, no file descriptors: a transport hands this module text or a decoded
frame and gets text or a dict back. That is what lets gating, binding, errors, version
negotiation and the probe lifecycle be tested with hand-built frames — the reason `profile.py`
and `apply_plan.py` are built the same way.

Three things are decided here and nowhere else:

* **What exists** (`Toolbox`): every entry is classified by what it *runs* (FR-1a) and anything
  not exposed is simply absent. Listing and calling read the same object, so there is no second
  path that skips the check (FR-3).
* **How a call runs** (`Dispatcher.call`): bad arguments and failing bridges become tool errors
  the model can read, never protocol errors (FR-5, FR-6); mutations are serialised and reads are
  never blocked (FR-8); the process-lifetime probe memos are kept honest (FR-8a).
* **What goes on the wire** (`Dispatcher.handle`): JSON-RPC 2.0 for MCP (FR-15–18).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from dev_setup import __version__
from dev_setup.agent import bridges
from dev_setup.agent.registry import AgentTool
from dev_setup.agent.sandbox import SandboxError

# -- Classification (FR-1a) -----------------------------------------------------------------


class Gate(StrEnum):
    READ_ONLY = "read-only"  # always exposed
    INSTALL = "install"  # only with --allow-install
    FUNCTION = "function"  # only with --allow-functions
    UNCLASSIFIED = "unclassified"  # never: an unknown catalog bridge fails closed
    PRIMITIVE = "primitive"  # never: workspace tools do not exist on this surface (FR-1)


# One row per bridge in `agent.bridges._BRIDGES`, keyed by *target* — what the entry runs.
# `test_every_bridge_has_a_gate` fails when a bridge is added without a row, so a new bridge
# cannot ship without someone deciding which flag, if any, guards it.
CATALOG_GATES: dict[str, Gate] = {
    "list_tools": Gate.READ_ONLY,
    "search_catalog": Gate.READ_ONLY,
    "tool_info": Gate.READ_ONLY,
    "outdated": Gate.READ_ONLY,
    "profile_diff": Gate.READ_ONLY,
    "install_tool": Gate.INSTALL,
}


def classify(tool: AgentTool) -> Gate:
    """The gate for one agent-toolbox entry, from `(impl, target)` and nothing else.

    Never from the key and never from `tool.mutating` alone: both are chosen by the user's
    `agent_tools.yaml`, which can rename `install_tool`, alias it, or label it harmless (F-10a).
    `mutating: true` can only move a read-only entry *into* a gate, never out of one.
    """
    if tool.impl == "primitive":
        return Gate.PRIMITIVE
    if tool.impl == "function":
        return Gate.FUNCTION
    if tool.impl == "catalog":
        gate = CATALOG_GATES.get(tool.target or "", Gate.UNCLASSIFIED)
        if gate is Gate.READ_ONLY and tool.mutating:
            return Gate.FUNCTION
        return gate
    return Gate.UNCLASSIFIED


def is_exposed(gate: Gate, *, allow_install: bool, allow_functions: bool) -> bool:
    """Whether a tool of this class exists for a server started with these flags (FR-3)."""
    if gate is Gate.READ_ONLY:
        return True
    if gate is Gate.INSTALL:
        return allow_install
    if gate is Gate.FUNCTION:
        return allow_functions
    return False


# -- The toolbox (FR-1, FR-16b, FR-16c) ---------------------------------------------------------

# The constraint Claude's tool API applies once a client prefixes a name (`mcp__devstuff__<key>`).
TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")
# Names the server itself uses. `GET /tools` is an OpenAPI operation named this (FR-22), so a tool
# with the same key would produce two operations with one operationId.
RESERVED_NAMES = frozenset({"openai_tool_list"})


class UnknownTool(LookupError):
    """The name is not in the toolbox — never existed, or exists behind a flag that is off.
    The two are deliberately indistinguishable to a caller (FR-3)."""


class Toolbox:
    """The tools a server started with these flags exposes, and the gate each sits behind."""

    def __init__(self, tools: Mapping[str, AgentTool], gates: Mapping[str, Gate]) -> None:
        self._tools = dict(tools)
        self._gates = dict(gates)

    @classmethod
    def from_agent(
        cls,
        agent_tools: Mapping[str, AgentTool],
        *,
        allow_install: bool,
        allow_functions: bool,
        warn: Callable[[str], None],
    ) -> Toolbox:
        """Filter the agent's effective toolbox down to what this surface may expose.

        A tool that is *expected* to be absent (a primitive, a gated tool whose flag is off) is
        dropped silently. One that is absent for a reason the user should know — an entry no
        classification covers, a name a client would reject — is dropped with a warning rather
        than breaking the client's whole `tools/list` (FR-16b).
        """
        tools: dict[str, AgentTool] = {}
        gates: dict[str, Gate] = {}
        for key, tool in agent_tools.items():
            gate = classify(tool)
            if gate is Gate.PRIMITIVE:
                continue
            if gate is Gate.UNCLASSIFIED:
                warn(
                    f"tool '{key}' left out: it runs '{tool.target or tool.impl}', which devstuff mcp "
                    "does not know how to gate, so it is not exposed"
                )
                continue
            if not is_exposed(gate, allow_install=allow_install, allow_functions=allow_functions):
                continue
            if not TOOL_NAME.fullmatch(key) or key in RESERVED_NAMES:
                warn(
                    f"tool '{key}' left out: a tool name must match [a-zA-Z0-9_-]{{1,64}} "
                    f"and not be reserved ({', '.join(sorted(RESERVED_NAMES))})"
                )
                continue
            tools[key] = tool
            gates[key] = gate
        return cls(tools, gates)

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def gate(self, name: str) -> Gate:
        return self._gates[name]

    def input_schema(self, tool: AgentTool) -> dict[str, Any]:
        """FR-16c: `required` omitted when empty (older JSON Schema drafts reject `[]`) and extra
        properties forbidden, so a client's schema check agrees with `bind()`'s own rule."""
        schema = tool.to_schema()["function"]["parameters"]
        out: dict[str, Any] = {"type": "object", "properties": schema["properties"]}
        if schema.get("required"):
            out["required"] = schema["required"]
        out["additionalProperties"] = False
        return out

    def requires_body(self, name: str) -> bool:
        """Whether a REST call to this tool must carry a body: it has a required parameter (FR-23)."""
        return bool(self.input_schema(self._tools[name]).get("required"))

    def openai_tools(self) -> list[dict[str, Any]]:
        """`GET /tools`: the OpenAI function-calling array, with FR-16c's schema rules (FR-21)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": key,
                    "description": tool.description,
                    "parameters": self.input_schema(tool),
                },
            }
            for key, tool in self._tools.items()
        ]

    def describe(self) -> list[dict[str, Any]]:
        """The MCP `tools/list` entries."""
        return [
            {
                "name": key,
                "title": tool.name,
                "description": tool.description,
                "inputSchema": self.input_schema(tool),
            }
            for key, tool in self._tools.items()
        ]


# -- Results (FR-7) --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """What a call produced. `note` is advisory and kept *out of* `text` so a JSON array in `text`
    stays parseable (FR-7): MCP carries it as a second text item, REST as its own field."""

    is_error: bool
    text: str
    note: str = ""


INSTALL_NOTE = "an install is in progress; this may change"


def to_mcp_result(result: ToolResult) -> dict[str, Any]:
    content = [{"type": "text", "text": result.text}]
    if result.note:
        content.append({"type": "text", "text": result.note})
    return {"content": content, "isError": result.is_error}


def rest_result(result: ToolResult) -> dict[str, Any]:
    """The REST body for a call: the same facts as MCP's result (FR-23). `note` only when there is one."""
    out: dict[str, Any] = {"is_error": result.is_error, "text": result.text}
    if result.note:
        out["note"] = result.note
    return out


def _install_hint(key: str) -> str:
    # FR-13. Appended to *every* install failure, not keyed on sudo's wording: under -v the error
    # is only "exit code 1" and sudo's line never reaches the result (F-13).
    return (
        f" If this needed a password, it cannot be answered here (the server has no terminal): "
        f"run `devstuff install {key}` in a terminal."
    )


# -- Dispatch (FR-3, FR-5, FR-6, FR-8, FR-8a) ----------------------------------------------------


def _invoke(tool: AgentTool, bound: dict[str, Any]) -> str:
    """Run one bound call. No workspace, no agent config (FR-4, F-5)."""
    if tool.impl == "function":
        return bridges.run_function(None, None, tool.target or "", bound)  # type: ignore[arg-type]
    bridge = bridges.get(tool.target or "")
    if bridge is None:  # unreachable while test_every_bridge_has_a_gate holds
        raise SandboxError(f"no bridge named '{tool.target}'")
    return bridge(None, None, bound)  # type: ignore[arg-type]


def _log_to_stderr(message: str) -> None:
    sys.stderr.write(message.rstrip("\n") + "\n")


def _forget_probes() -> None:
    from dev_setup import generic

    generic.forget_probes()


class Dispatcher:
    """Runs calls against a `Toolbox` and speaks JSON-RPC for them.

    `run`, `forget` and `log_error` are injectable so the state machine below is tested with a
    fake machine; the defaults are the real bridges, `generic.forget_probes` and stderr.
    """

    def __init__(
        self,
        toolbox: Toolbox,
        *,
        run: Callable[[AgentTool, dict[str, Any]], str] = _invoke,
        forget: Callable[[], None] = _forget_probes,
        log_error: Callable[[str], None] = _log_to_stderr,
    ) -> None:
        self.toolbox = toolbox
        self._run = run
        self._forget = forget
        self._log_error = log_error
        # FR-8: one mutation at a time, across *both* gated classes — apt's lock and shared
        # prefixes do not care which flag started the install.
        self._mutation = threading.Lock()
        # FR-8a: the state below is guarded by `_state`. `_mutations` is bumped when a gated call
        # starts and again when it ends, so a read that sees it unchanged saw no mutation at all.
        self._state = threading.Lock()
        self._readers = 0
        self._mutations = 0
        self._running = 0

    # -- one call ---------------------------------------------------------------------------

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Run a tool. Raises `UnknownTool` for a name that is absent or gated off; everything
        else — bad arguments, a bridge that fails, one that crashes — is a `ToolResult`."""
        tool = self.toolbox.get(name)
        if tool is None:
            raise UnknownTool(name)
        try:
            bound = tool.bind(arguments or {})
        except ValueError as exc:
            return ToolResult(True, str(exc))

        if self.toolbox.gate(name) is Gate.READ_ONLY:
            return self._read(tool, bound)
        return self._mutate(tool, bound)

    def _execute(self, tool: AgentTool, bound: dict[str, Any]) -> ToolResult:
        """One run of one bridge, with every failure turned into a result (FR-6)."""
        try:
            return ToolResult(False, self._run(tool, bound))
        except bridges.InstallFailed as exc:
            return ToolResult(True, str(exc) + _install_hint(str(bound.get("key", "<key>"))))
        except SandboxError as exc:
            return ToolResult(True, str(exc))
        except SystemExit as exc:
            # A bridge that exits would end its worker thread without ever answering the client.
            return ToolResult(True, f"{tool.key} exited unexpectedly (status {exc.code})")
        except Exception as exc:
            self._log_error(f"{tool.key}: {traceback.format_exc()}")
            first = (str(exc).splitlines() or [""])[0]
            return ToolResult(True, f"{type(exc).__name__}: {first}" if first else type(exc).__name__)

    def _mutate(self, tool: AgentTool, bound: dict[str, Any]) -> ToolResult:
        with self._mutation:
            with self._state:
                self._mutations += 1
                self._running += 1
            try:
                return self._execute(tool, bound)
            finally:
                # In `finally`: a failure part-way may already have changed the machine, and a
                # memo that says otherwise would be served until the process exits (FR-8a.2).
                try:
                    self._forget()
                finally:
                    with self._state:
                        self._mutations += 1
                        self._running -= 1

    def _read(self, tool: AgentTool, bound: dict[str, Any]) -> ToolResult:
        """Run a read-only tool: never blocked by a mutation, never served stale (FR-8a)."""
        with self._state:
            new_burst = self._readers == 0
            self._readers += 1
        try:
            if new_burst:
                # Outside `_state`: clearing takes the lock of any probe still running, and that wait
                # must not stall every other request's bookkeeping. A concurrent reader that joins in
                # the gap at worst re-probes; it can never be handed a half-cleared memo.
                self._forget()
            result, straddled, installing = self._read_once(tool, bound)
            if straddled and not installing and not result.is_error:
                # A mutation began and ended during this read, so it may hold one memo from before
                # and one from after. The mutation is finished, so one clean re-run is exact.
                self._forget()
                result, straddled, installing = self._read_once(tool, bound)
            if (straddled or installing) and not result.is_error:
                result = ToolResult(False, result.text, INSTALL_NOTE)
            return result
        finally:
            with self._state:
                self._readers -= 1

    def _read_once(self, tool: AgentTool, bound: dict[str, Any]) -> tuple[ToolResult, bool, bool]:
        with self._state:
            before = self._mutations
        result = self._execute(tool, bound)
        with self._state:
            return result, self._mutations != before, self._running > 0

    # -- JSON-RPC (FR-15 … FR-18) -------------------------------------------------------------

    def handle_text(self, text: str) -> str | None:
        """One frame of text in, one frame of text out (None for a notification)."""
        try:
            frame = json.loads(text)
        except ValueError:
            return _dump(_error(None, -32700, "parse error"))
        reply = self.handle(frame)
        return None if reply is None else _dump(reply)

    def handle(self, frame: Any) -> dict[str, Any] | None:
        """One decoded frame in, one reply out. Never raises."""
        if not isinstance(frame, dict):
            # A batch is a JSON array (FR-18); anything else that is not an object is invalid too.
            what = "batch requests are not supported" if isinstance(frame, list) else "invalid request"
            return _error(None, -32600, what)
        # A notification is the *absence* of the `id` key. Never a falsy value: Claude Code sends an
        # integer 0 for `initialize` and an empty-looking id must still be answered (FR-16a).
        notification = "id" not in frame
        rid = frame.get("id")
        method = frame.get("method")
        if not isinstance(method, str):
            return _error(None if notification else rid, -32600, "invalid request: no method")
        try:
            result = self._method(method, frame.get("params"))
        except _RpcError as exc:
            reply = _error(rid, exc.code, exc.message)
        else:
            reply = {"jsonrpc": "2.0", "id": rid, "result": result}
        return None if notification else reply

    def _method(self, method: str, params: Any) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.toolbox.describe()}
        if method == "tools/call":
            return self._tools_call(params)
        # Everything else — `server/discover`, resources/*, prompts/*, notifications — is "method not
        # found", which is JSON-RPC's own answer for a method a server does not implement. The current
        # official client opens with `server/discover` and falls back to `initialize`. Neither real client
        # *enforces* -32601 (both also fall back on `{}`, F-16), so only a raw-frame test pins it.
        raise _RpcError(-32601, f"method not found: {method}")

    def _initialize(self, params: Any) -> dict[str, Any]:
        wanted = params.get("protocolVersion") if isinstance(params, dict) else None
        # Echo a version we support; otherwise answer with our own latest and let the client decide.
        # Never refuse a session over a version (FR-17).
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "devstuff", "version": __version__},
        }

    def _tools_call(self, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise _RpcError(-32602, "invalid params: 'name' (a string) is required")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _RpcError(-32602, "invalid params: 'arguments' must be an object")
        name = params["name"]
        try:
            return to_mcp_result(self.call(name, arguments))
        except UnknownTool:
            raise _RpcError(-32602, f"unknown tool: {name}") from None


# Newest first. The only version the real client was measured to send is 2025-11-25 (F-2, F-3).
PROTOCOL_VERSIONS = ("2025-11-25",)


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def error_text(rid: Any, code: int, message: str) -> str:
    """A ready-to-send error frame, for a transport that must answer before a frame is parsed."""
    return _dump(_error(rid, code, message))


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _dump(frame: dict[str, Any]) -> str:
    # ensure_ascii escapes U+2028 and friends as well as newlines, so a frame can never contain
    # a character any line-oriented reader might split on (FR-19).
    return json.dumps(frame, separators=(",", ":"))
