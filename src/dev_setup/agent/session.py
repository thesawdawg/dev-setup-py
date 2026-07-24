from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.markdown import Markdown

from dev_setup import ui
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.ollama import Message, OllamaClient, OllamaError
from dev_setup.agent.sandbox import Workspace

STATE_DIR = Path.home() / ".local" / "share" / "dev-setup" / "agent"
HISTORY_PATH = STATE_DIR / "history"

SYSTEM_PROMPT = """You are the devstuff agent, a concise assistant running inside a \
terminal on a Linux developer machine.

Answer briefly and concretely. Prefer short paragraphs and code blocks over long prose. \
When you are unsure, say so rather than guessing.

You do not currently have any tools available, so you cannot read files, run commands, \
or change anything on this machine. If the user asks you to perform an action, explain \
what they should run instead."""

_SLASH_HELP = [
    ("/help", "Show this help"),
    ("/reset", "Clear the conversation history"),
    ("/exit", "End the session (also Ctrl-D)"),
]


class AgentSession:
    def __init__(
        self,
        client: OllamaClient,
        config: AgentConfig,
        *,
        model: str | None = None,
        workspace: Workspace | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.config = config
        self.model = model or config.model
        self.workspace = workspace
        self.system_prompt = system_prompt
        self.messages: list[dict[str, Any]] = []
        self.reset()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def send(self, prompt: str) -> Message:
        """One turn: append the user message, call the model, append the reply."""
        self.messages.append({"role": "user", "content": prompt})
        reply = self.client.chat(
            self.messages,
            model=self.model,
            temperature=self.config.temperature,
            num_ctx=self.config.num_ctx,
            think=self.config.think,
        )
        self.messages.append({"role": "assistant", "content": reply.content})
        return reply


def render(reply: Message, *, show_thinking: bool) -> None:
    if show_thinking and reply.thinking:
        ui.console.print(f"  [dim italic]{reply.thinking.strip()}[/]")
        ui.console.print()

    if reply.content.strip():
        ui.console.print(Markdown(reply.content.strip()))
    elif reply.tool_calls:
        # Nothing is wired up to execute these yet; say so rather than printing a blank.
        names = ", ".join(c.name for c in reply.tool_calls)
        ui.warn(f"The model tried to call a tool ({names}), but tools are not enabled yet.")
    else:
        ui.dim("(empty response)")
    ui.console.print()


def _print_banner(session: AgentSession) -> None:
    ui.section("devstuff agent")
    ui.dim(f"model    {session.model}")
    ui.dim(f"host     {session.client.host}")
    ui.dim(f"context  {session.config.num_ctx} tokens")
    if session.workspace:
        ui.dim(f"workspace {session.workspace.root}")
    ui.console.print()
    ui.dim("Tools are not enabled yet — this is a chat-only preview.")
    ui.dim("/help for commands, /exit or Ctrl-D to quit.")
    ui.console.print()


def _print_slash_help() -> None:
    for cmd, desc in _SLASH_HELP:
        ui.console.print(f"  [bold cyan]{cmd:<8}[/]  {desc}")
    ui.console.print()


def _handle_slash(session: AgentSession, line: str) -> bool:
    """Returns True if the session should end."""
    cmd = line.split()[0].lower()
    if cmd in ("/exit", "/quit"):
        return True
    if cmd == "/help":
        _print_slash_help()
    elif cmd == "/reset":
        session.reset()
        ui.success("Conversation cleared.")
        ui.console.print()
    else:
        ui.error(f"Unknown command: {cmd}")
        _print_slash_help()
    return False


def _turn(session: AgentSession, line: str) -> None:
    try:
        with ui.spinner("thinking…"):
            reply = session.send(line)
    except KeyboardInterrupt:
        # Drop the user message we optimistically appended so the history stays a
        # clean alternating transcript for the next turn.
        if session.messages and session.messages[-1]["role"] == "user":
            session.messages.pop()
        ui.console.print()
        ui.dim("(cancelled)")
        ui.console.print()
        return
    except OllamaError as exc:
        if session.messages and session.messages[-1]["role"] == "user":
            session.messages.pop()
        ui.error(str(exc))
        ui.console.print()
        return
    render(reply, show_thinking=session.config.think)


def run_repl(session: AgentSession) -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession = PromptSession(history=FileHistory(str(HISTORY_PATH)))

    _print_banner(session)

    while True:
        try:
            line = prompt_session.prompt("you ❯ ").strip()
        except KeyboardInterrupt:
            continue  # Ctrl-C clears the current line, like a shell
        except EOFError:
            break

        if not line:
            continue
        if line.startswith("/"):
            if _handle_slash(session, line):
                break
            continue

        _turn(session, line)

    ui.console.print()
    ui.dim("Session ended.")


def run_once(session: AgentSession, prompt: str) -> None:
    with ui.spinner("thinking…"):
        reply = session.send(prompt)
    render(reply, show_thinking=session.config.think)
