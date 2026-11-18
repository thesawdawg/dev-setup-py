from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.markdown import Markdown
from rich.text import Text

from dev_setup import ui
from dev_setup.agent import loop, registry
from dev_setup.agent.approval import ApprovalPolicy
from dev_setup.agent.config import AgentConfig
from dev_setup.agent.ollama import ChatClient, Message, OllamaError
from dev_setup.agent.registry import AgentTool
from dev_setup.agent.sandbox import Workspace
from dev_setup.agent.transcript import Transcript

if TYPE_CHECKING:
    from dev_setup.agent.scoped import ScopedActions

STATE_DIR = Path.home() / ".local" / "share" / "devstuff" / "agent"
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
    ("/history", "Show this session's turns, and where the transcript is saved"),
    ("/cwd", "Show the workspace root and current directory"),
    ("/model", "Show the model in use"),
    ("/reset", "Clear the conversation history"),
    ("/help", "Show this help"),
    ("/exit", "End the session (also Ctrl-D)"),
]


class AgentSession:
    def __init__(
        self,
        client: ChatClient,
        config: AgentConfig,
        workspace: Workspace,
        *,
        model: str | None = None,
        tools: dict[str, AgentTool] | None = None,
        policy: ApprovalPolicy | None = None,
        transcript: Transcript | None = None,
        scoped_actions: ScopedActions | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.workspace = workspace
        self.model = model or config.model
        self.scoped_actions = scoped_actions
        if scoped_actions is not None:
            # A scoped session's tools are exactly its fixed mapping — an
            # explicit `tools` argument must not widen the scoped surface.
            self.tools = scoped_actions.tools
        elif tools is not None:
            self.tools = tools
        else:
            self.tools = registry.build()
        self.schemas = registry.to_schemas(self.tools)
        self.policy = policy or ApprovalPolicy(auto_approve=config.auto_approve)
        self.transcript = transcript
        self.messages: list[dict[str, Any]] = []
        self.reset()

    def system_prompt(self) -> str:
        if self.scoped_actions is not None:
            # Lazy import: scoped pulls in the loop and catalogs, which must
            # not become a load-time cycle with this module.
            from dev_setup.agent.scoped import SYSTEM_PROMPT as SCOPED_PROMPT

            return SCOPED_PROMPT
        return SYSTEM_PROMPT.format(
            root=self.workspace.root, cwd=self.workspace.display(self.workspace.cwd)
        )

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt()}]
        if self.scoped_actions is not None:
            self.scoped_actions.reset()

    def send(self, prompt: str) -> Message | None:
        try:
            return loop.run_turn(self, prompt)
        finally:
            # In `finally` so a cancelled or failed turn is still recorded -- those
            # are the ones worth reading back.
            if self.transcript:
                self.transcript.record(self.messages)


def render(reply: Message | None) -> None:
    if reply is None:
        ui.console.print()
        return
    if reply.content.strip():
        ui.console.print()
        ui.console.print(ui.gutter(Markdown(reply.content.strip())))
    ui.console.print()


def _print_banner(session: AgentSession, *, browse_catalog=None) -> None:
    ui.section("devstuff agent")
    rows = [
        ("model", session.model),
        ("host", session.client.host),
    ]
    if getattr(session, "scoped_actions", None) is not None:
        rows.append(("mode", "Find tools, review runs, or register an extension"))
    else:
        rows.append(("workspace", str(session.workspace.root)))
    rows.append(("tools", f"{len(session.tools)} available"))
    for key, value in rows:
        ui.console.print(f"  [{ui.PURPLE}]{key:<10}[/] [{ui.GRAY}]{value}[/]")
    if session.policy.yolo:
        ui.console.print()
        ui.warn("--yolo: mutating tools run without confirmation (the denylist still applies).")
    ui.console.print()
    ui.dim("/ for commands and tools · Alt+Enter for a newline · Ctrl-D to quit")
    if browse_catalog is not None:
        ui.dim("/browse opens the read-only catalog browser")
    ui.console.print()


def _print_slash_help(
    commands: list[tuple[str, str]] | None = None, *, tool_example: str = "write_file"
) -> None:
    for cmd, desc in commands or _SLASH_HELP:
        ui.console.print(f"  [bold cyan]{cmd:<9}[/]  {desc}")
    ui.console.print(
        f"  [bold cyan]{'/<tool>':<9}[/]  Describe one tool, e.g. /{tool_example}"
    )
    ui.console.print()
    ui.dim("  Type / for a completion menu of commands and tools.")
    ui.dim("  Enter sends · Alt+Enter (or Ctrl-J) inserts a newline")
    ui.console.print()


def _print_tools(session: AgentSession) -> None:
    for key, tool in sorted(session.tools.items()):
        marker = f"[{ui.AMBER}]![/]" if tool.mutating else " "
        ui.console.print(f"  {marker} [bold cyan]{key:<18}[/] [dim]{tool.description[:70]}[/]")
    ui.console.print()
    ui.dim("! = mutating, asks for confirmation before running")
    ui.console.print()


def _print_tool_detail(tool: AgentTool) -> None:
    ui.console.print()
    ui.console.print(f"  [bold cyan]{tool.key}[/]  [dim]{tool.name}[/]")
    ui.console.print(f"  {tool.description}")
    ui.console.print()
    if tool.mutating:
        ui.warn("Mutating — asks for confirmation before it runs.")
    if not tool.params:
        ui.dim("  (no parameters)")
    for param in tool.params:
        flag = "required" if param.required else "optional"
        default = f", default {param.default!r}" if param.default is not None else ""
        ui.console.print(f"  [cyan]{param.name}[/] [dim]({param.type}, {flag}{default})[/]")
        if param.description:
            ui.console.print(f"      [dim]{param.description}[/]")
    ui.console.print()


def _summarise(message: dict[str, Any]) -> str | None:
    role = message.get("role")
    if role == "system":
        return None
    if role == "user":
        return f"[{ui.PURPLE} bold]you[/]     {str(message.get('content', ''))[:80]}"
    if role == "tool":
        first = str(message.get("content", "")).splitlines()[:1]
        return f"[{ui.GRAY}]tool[/]     {message.get('tool_name', '?')} → {(first[0] if first else '')[:60]}"
    calls = message.get("tool_calls") or []
    if calls:
        names = ", ".join(c["function"]["name"] for c in calls)
        return f"[{ui.CYAN}]agent[/]    calls {names}"
    return f"[{ui.CYAN}]agent[/]    {str(message.get('content', ''))[:80]}"


def _print_history(session: AgentSession) -> None:
    lines = [s for s in (_summarise(m) for m in session.messages) if s]
    if not lines:
        ui.dim("Nothing yet this session.")
    for line in lines:
        ui.console.print(f"  {line}")
    ui.console.print()
    if session.transcript:
        ui.dim(f"Full transcript: {session.transcript.path}")
        ui.console.print()


def _slash_commands(
    session: AgentSession, browse_catalog: Callable[[], None] | None
) -> list[tuple[str, str]]:
    """The `/` menu for this session: `/cwd` means nothing to a scoped session
    (there is no workspace to show), `/browse` only exists when wired."""
    commands = [
        entry for entry in _SLASH_HELP
        if entry[0] != "/cwd" or getattr(session, "scoped_actions", None) is None
    ]
    if browse_catalog is not None:
        commands.append(("/browse", "Browse the tool catalog"))
    return commands


def _handle_slash(
    session: AgentSession,
    line: str,
    *,
    commands: list[tuple[str, str]] | None = None,
    browse_catalog: Callable[[], None] | None = None,
) -> bool:
    """Returns True if the session should end."""
    scoped = getattr(session, "scoped_actions", None) is not None
    tool_example = "find_tools" if scoped else "write_file"
    cmd = line.split()[0].lower()
    if cmd in ("/exit", "/quit"):
        return True
    if cmd == "/help":
        _print_slash_help(commands, tool_example=tool_example)
    elif cmd == "/browse" and browse_catalog is not None:
        try:
            browse_catalog()
        except click.ClickException as exc:
            # A broken catalog ends the browse, not the chat.
            ui.console.print(Text(str(exc)))
    elif cmd == "/tools":
        _print_tools(session)
    elif cmd == "/cwd" and not scoped:
        ui.dim(f"workspace  {session.workspace.root}")
        ui.dim(f"current    {session.workspace.display(session.workspace.cwd)}")
        ui.console.print()
    elif cmd == "/model":
        ui.dim(f"{session.model} @ {session.client.host}")
        ui.console.print()
    elif cmd == "/history":
        _print_history(session)
    elif cmd == "/reset":
        session.reset()
        ui.success("Conversation cleared.")
        ui.console.print()
    elif cmd.lstrip("/") in session.tools:
        # `/<tool>` is completable, so it has to do something sensible. It describes
        # the tool rather than running it -- the agent decides when tools run, and a
        # REPL back door around the confirmation flow is not worth the ambiguity.
        _print_tool_detail(session.tools[cmd.lstrip("/")])
    else:
        ui.error(f"Unknown command: {cmd}")
        _print_slash_help(commands, tool_example=tool_example)
    return False


def _turn(session: AgentSession, line: str) -> None:
    start = len(session.messages)
    try:
        reply = session.send(line)
    except KeyboardInterrupt:
        # Roll the whole turn back: a half-finished tool exchange left in
        # history — an assistant call without its tool results — would be
        # rejected by the next request.
        del session.messages[start:]
        ui.console.print()
        ui.dim("(cancelled)")
        ui.console.print()
        return
    except OllamaError as exc:
        del session.messages[start:]
        ui.console.print(Text(str(exc)))
        ui.console.print()
        return
    render(reply)


def _key_bindings():
    """Enter submits, Alt+Enter (or Ctrl-J) inserts a newline.

    prompt_toolkit's `multiline=True` defaults to the opposite -- Enter inserts and
    Meta+Enter submits -- which is wrong for a chat prompt where most turns are one
    line. Ctrl-J is bound too because many terminals cannot distinguish Shift+Enter
    and send it instead.
    """
    from prompt_toolkit.filters import completion_is_selected
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    @kb.add("c-j")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    # Bound explicitly: under multiline=True, Tab is not wired to completion by
    # default, so relying on prompt_toolkit's defaults silently gives no menu.
    @kb.add("tab")
    def _complete(event):
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=True)

    @kb.add("s-tab")
    def _complete_backwards(event):
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()

    # Registered after the plain binding so it wins while the menu is open: Enter
    # then takes the highlighted completion instead of sending a half-typed line.
    @kb.add("enter", filter=completion_is_selected)
    def _accept_completion(event):
        event.current_buffer.complete_state = None

    return kb


def _continuation(width: int, line_number: int, is_soft_wrap: bool) -> str:
    return " " * max(0, width - 2) + "› "


def _prompt_style():
    """prompt_toolkit theme: violet prompt glyph, dark-violet completion menu."""
    from prompt_toolkit.styles import Style as PTStyle

    return PTStyle.from_dict({
        "prompt": f"{ui.PURPLE} bold",
        "completion-menu": "bg:#26223B #DDD6FE",
        "completion-menu.completion": "bg:#26223B #DDD6FE",
        "completion-menu.completion.current": f"bg:{ui.VIOLET} #FFFFFF bold",
        "completion-menu.meta.completion": f"bg:#26223B {ui.GRAY}",
        "completion-menu.meta.completion.current": "bg:#7C3AED #DDD6FE",
        "scrollbar.background": "bg:#26223B",
        "scrollbar.button": "bg:#4B4568",
    })


def _scoped_prompt_style():
    """Scoped prompt theme: foreground only — the scoped surface never paints
    backgrounds, so the completion menu marks the current row with
    weight/underline instead of a highlight block."""
    from prompt_toolkit.styles import Style as PTStyle

    return PTStyle.from_dict({
        "prompt": f"{ui.CYAN} bold",
        "completion-menu": ui.GRAY,
        "completion-menu.completion.current": f"{ui.CYAN} bold underline",
    })


def build_prompt_session(session: AgentSession, commands=None, **overrides):
    """Split out from run_repl so the input behaviour can be driven by tests with a
    pipe input rather than only exercised by hand."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.output import ColorDepth

    from dev_setup.agent.completion import SlashCompleter

    scoped = getattr(session, "scoped_actions", None) is not None
    no_color = "NO_COLOR" in os.environ or ui.console.no_color
    kwargs = {
        # Scoped sessions keep nothing on disk: the prompt history would be a
        # second transcript of a session that was told it leaves none.
        "history": (
            InMemoryHistory() if scoped else FileHistory(str(HISTORY_PATH))
        ),
        "completer": SlashCompleter(commands or _SLASH_HELP, session.tools),
        "complete_while_typing": True,
        "multiline": True,
        "key_bindings": _key_bindings(),
        "prompt_continuation": _continuation,
        "style": _scoped_prompt_style() if scoped else _prompt_style(),
    }
    if scoped:
        # Strip any inherited reverse/background rendering; 1-bit when the
        # user asked for no colour.
        kwargs["style_transformation"] = ui._PromptStyle(no_color)
        if no_color:
            kwargs["color_depth"] = ColorDepth.DEPTH_1_BIT
    kwargs.update(overrides)
    return PromptSession(**kwargs)


def run_repl(
    session: AgentSession, *, browse_catalog: Callable[[], None] | None = None
) -> None:
    commands = _slash_commands(session, browse_catalog)

    prompt_session = None
    if not ui._static_prompt():
        if getattr(session, "scoped_actions", None) is None:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
        prompt_session = build_prompt_session(session, commands=commands)

    _print_banner(session, browse_catalog=browse_catalog)

    from prompt_toolkit.formatted_text import HTML

    while True:
        try:
            if prompt_session is not None:
                line = prompt_session.prompt(HTML("<prompt>you ❯</prompt> ")).strip()
            else:
                # TERM=dumb / DEVSTUFF_PLAIN: a static line read, no editing
                # chrome — the same fallback plain prompts use elsewhere.
                line = ui.console.input("you ❯ ").strip()
        except KeyboardInterrupt:
            continue  # Ctrl-C clears the current line, like a shell
        except EOFError:
            break

        if not line:
            continue
        # A multi-line message that happens to open with "/" is prose, not a command.
        if line.startswith("/") and "\n" not in line:
            if _handle_slash(
                session, line, commands=commands, browse_catalog=browse_catalog
            ):
                break
            continue

        _turn(session, line)

    ui.console.print()
    ui.dim("Session ended.")


def run_once(session: AgentSession, prompt: str) -> None:
    render(session.send(prompt))
