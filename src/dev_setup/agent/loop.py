from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.markup import escape

from dev_setup import ui
from dev_setup.agent import bridges, primitives
from dev_setup.agent.ollama import Message, ToolCall
from dev_setup.agent.registry import AgentTool
from dev_setup.agent.sandbox import SandboxError


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    content: str
    truncated: bool = False


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cap tool output so one `ls -R` cannot swallow the context window."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    kept = encoded[:limit].decode("utf-8", errors="ignore")
    dropped = len(encoded) - len(kept.encode("utf-8"))
    return f"{kept}\n\n[... {dropped} bytes truncated -- narrow the request to see more]", True


def _dispatch(session, tool: AgentTool, args: dict[str, Any]) -> str:
    if tool.impl == "primitive":
        fn = primitives.get(tool.key)
        if fn is None:
            raise SandboxError(f"'{tool.key}' has no implementation")
        return fn(session.workspace, session.config, args)

    if tool.impl == "catalog":
        bridge = bridges.get(tool.target or "")
        if bridge is None:
            raise SandboxError(f"'{tool.key}' has no implementation")
        return bridge(session.workspace, session.config, args)

    if tool.impl == "function":
        return bridges.run_function(session.workspace, session.config, tool.target or "", args)

    raise SandboxError(f"'{tool.key}' has an unsupported impl '{tool.impl}'")


def execute(session, call: ToolCall) -> ToolOutcome:
    """Run one tool call. Every failure mode returns a ToolOutcome rather than
    raising: the model needs to see what went wrong to correct itself, and a
    malformed call must never end the session."""
    # A scoped session has its own fixed tools and approval flow — nothing
    # here (workspace tools, legacy mutation policy) applies to it.
    scoped = getattr(session, "scoped_actions", None)
    if scoped is not None:
        return scoped.execute(call, session.config.max_tool_output_bytes)

    tool = session.tools.get(call.name)
    if tool is None:
        known = ", ".join(sorted(session.tools)) or "none"
        return ToolOutcome(call.name, False, f"unknown tool '{call.name}'. Available tools: {known}")

    try:
        args = tool.bind(call.arguments)
    except ValueError as exc:
        return ToolOutcome(call.name, False, f"invalid call: {exc}")

    if session.policy.needs_confirmation(tool):
        if not session.policy.confirm(tool, args, session.workspace):
            return ToolOutcome(
                call.name, False, "the user declined this action. Ask what they would prefer."
            )
    else:
        preview = ", ".join(f"{k}={v!r}" for k, v in args.items())[:120]
        ui.console.print(f"  [{ui.CYAN}]▸[/] [{ui.GRAY}]{call.name}({preview})[/]")

    try:
        output = _dispatch(session, tool, args)
    except SandboxError as exc:
        return ToolOutcome(call.name, False, f"error: {exc}")
    except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the session
        return ToolOutcome(call.name, False, f"error: {type(exc).__name__}: {exc}")

    content, was_truncated = truncate(output, session.config.max_tool_output_bytes)
    return ToolOutcome(call.name, True, content, truncated=was_truncated)


def _assistant_message(reply: Message) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        calls = []
        for c in reply.tool_calls:
            entry: dict[str, Any] = {
                "function": {"name": c.name, "arguments": c.arguments}
            }
            if c.id is not None:
                # OpenAI-style ids round-trip into history; Ollama calls have
                # none and the message keeps the legacy shape.
                entry["id"] = c.id
            calls.append(entry)
        msg["tool_calls"] = calls
    return msg


def run_turn(session, user_text: str) -> Message | None:
    """Drive one user turn to completion: model, tool calls, results, repeat."""
    start = len(session.messages)
    session.messages.append({"role": "user", "content": user_text})

    for _ in range(session.config.max_iterations):
        with ui.spinner("thinking…"):
            reply = session.client.chat(
                session.messages,
                model=session.model,
                tools=session.schemas,
                temperature=session.config.temperature,
                num_ctx=session.config.num_ctx,
                think=session.config.think,
            )

        if reply.thinking and session.config.think:
            ui.console.print(f"  [dim italic]{reply.thinking.strip()}[/]")

        session.messages.append(_assistant_message(reply))

        if not reply.tool_calls:
            return reply

        for call in reply.tool_calls:
            outcome = execute(session, call)
            result: dict[str, Any] = {
                "role": "tool",
                "tool_name": outcome.name,
                "content": outcome.content,
            }
            if call.id is not None:
                result["tool_call_id"] = call.id
            session.messages.append(result)
            if not outcome.ok:
                ui.console.print(
                    f"  [{ui.AMBER}]▸[/] "
                    f"[{ui.GRAY}]{escape(outcome.content.splitlines()[0][:120])}[/]"
                )

    # An exhausted turn is a half-finished exchange — an assistant call
    # possibly awaiting results — so it doesn't stay in history.
    del session.messages[start:]
    ui.warn(f"Stopped after {session.config.max_iterations} tool calls without a final answer.")
    ui.dim("Ask again with a narrower request, or /reset to start over.")
    return None
