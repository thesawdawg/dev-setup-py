from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.markdown import Markdown

from dev_setup import ui
from dev_setup.agent import loop, registry
from dev_setup.agent.approval import ApprovalPolicy
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.ollama import Message, OllamaClient, OllamaError
from dev_setup.agent.registry import AgentTool
from dev_setup.agent.sandbox import Workspace

STATE_DIR = Path.home() / ".local" / "share" / "dev-setup" / "agent"
HISTORY_PATH = STATE_DIR / "history"

SYSTEM_PROMPT = """You are the devstuff agent, working in a terminal on a Linux \
developer machine. You complete tasks by calling tools.

Rules:
- Use tools to do real work. Do not describe what you would do -- do it.
- Call one tool at a time and read its result before the next call.
- Read a file before overwriting it. write_file replaces the whole file.
- To install developer tools, use install_tool. Never use sudo or a package \
manager through run_command; it will be refused.
- All work happens inside the workspace directory. Paths outside it are refused.
- When the task is done, reply with a short plain-text summary and no further \
tool calls.

Workspace: {root}
Current directory: {cwd}"""

_SLASH_HELP = [
    ("/tools", "List the tools available to the agent"),
    ("/cwd", "Show the workspace root and current directory"),
    ("/model", "Show the model in use"),
    ("/reset", "Clear the conversation history"),
    ("/help", "Show this help"),
    ("/exit", "End the session (also Ctrl-D)"),
]


class AgentSession:
    def __init__(
        self,
        client: OllamaClient,
        config: AgentConfig,
        workspace: Workspace,
        *,
        model: str | None = None,
        tools: dict[str, AgentTool] | None = None,
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.workspace = workspace
        self.model = model or config.model
        self.tools = tools if tools is not None else registry.build()
        self.schemas = registry.to_schemas(self.tools)
        self.policy = policy or ApprovalPolicy(auto_approve=config.auto_approve)
        self.messages: list[dict[str, Any]] = []
        self.reset()

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(
            root=self.workspace.root, cwd=self.workspace.display(self.workspace.cwd)
        )

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt()}]

    def send(self, prompt: str) -> Message | None:
        return loop.run_turn(self, prompt)


def render(reply: Message | None) -> None:
    if reply is None:
        ui.console.print()
        return
    if reply.content.strip():
        ui.console.print()
        ui.console.print(Markdown(reply.content.strip()))
    ui.console.print()


def _print_banner(session: AgentSession) -> None:
    ui.section("devstuff agent")
    ui.dim(f"model      {session.model}")
    ui.dim(f"host       {session.client.host}")
    ui.dim(f"workspace  {session.workspace.root}")
    ui.dim(f"tools      {len(session.tools)} available")
    if session.policy.yolo:
        ui.console.print()
        ui.warn("--yolo: mutating tools run without confirmation (the denylist still applies).")
    ui.console.print()
    ui.dim("/help for commands, /exit or Ctrl-D to quit.")
    ui.console.print()


def _print_slash_help() -> None:
    for cmd, desc in _SLASH_HELP:
        ui.console.print(f"  [bold cyan]{cmd:<8}[/]  {desc}")
    ui.console.print()


def _print_tools(session: AgentSession) -> None:
    for key, tool in sorted(session.tools.items()):
        marker = "[yellow]![/]" if tool.mutating else " "
        ui.console.print(f"  {marker} [bold cyan]{key:<18}[/] [dim]{tool.description[:70]}[/]")
    ui.console.print()
    ui.dim("! = mutating, asks for confirmation before running")
    ui.console.print()


def _handle_slash(session: AgentSession, line: str) -> bool:
    """Returns True if the session should end."""
    cmd = line.split()[0].lower()
    if cmd in ("/exit", "/quit"):
        return True
    if cmd == "/help":
        _print_slash_help()
    elif cmd == "/tools":
        _print_tools(session)
    elif cmd == "/cwd":
        ui.dim(f"workspace  {session.workspace.root}")
        ui.dim(f"current    {session.workspace.display(session.workspace.cwd)}")
        ui.console.print()
    elif cmd == "/model":
        ui.dim(f"{session.model} @ {session.client.host}")
        ui.console.print()
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
        reply = session.send(line)
    except KeyboardInterrupt:
        # Roll back to the last completed turn: a half-finished tool exchange left
        # in the history would confuse the next request.
        _rollback(session)
        ui.console.print()
        ui.dim("(cancelled)")
        ui.console.print()
        return
    except OllamaError as exc:
        _rollback(session)
        ui.error(str(exc))
        ui.console.print()
        return
    render(reply)


def _rollback(session: AgentSession) -> None:
    while session.messages and session.messages[-1]["role"] != "assistant":
        session.messages.pop()


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
    render(session.send(prompt))
