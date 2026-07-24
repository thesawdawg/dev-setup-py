from __future__ import annotations

import difflib
from typing import Any

from rich.panel import Panel
from rich.syntax import Syntax

from dev_setup import ui
from dev_setup.agent.registry import AgentTool
from dev_setup.agent.sandbox import SandboxError, Workspace

_YES = "Yes, run it"
_NO = "No, skip it"
_ALWAYS = "Always allow this tool for the rest of the session"


def _write_file_preview(args: dict[str, Any], ws: Workspace) -> Panel:
    try:
        path = ws.resolve(args.get("path", ""), write=True)
    except SandboxError:
        # The sandbox will reject this before it runs; show the raw request rather
        # than crashing the prompt.
        return Panel(str(args.get("path", "")), title="write_file", border_style="yellow")

    new = args.get("content", "")
    old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    label = ws.display(path)

    if not path.exists():
        body = Syntax(new, "text", theme="monokai", line_numbers=False, word_wrap=True)
        return Panel(body, title=f"create {label}", border_style="green")

    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=label,
            tofile=label,
            n=2,
        )
    )
    if not diff:
        return Panel("(no change)", title=f"write {label}", border_style="dim")
    return Panel(
        Syntax(diff, "diff", theme="monokai", line_numbers=False),
        title=f"edit {label}",
        border_style="yellow",
    )


def preview(tool: AgentTool, args: dict[str, Any], ws: Workspace) -> Panel:
    """What the user is being asked to approve. Must show the real effect -- the
    exact command, or the exact diff -- not a paraphrase of it."""
    if tool.key == "write_file":
        return _write_file_preview(args, ws)

    if tool.key == "run_command":
        return Panel(
            Syntax(str(args.get("command", "")), "bash", theme="monokai", line_numbers=False),
            title=f"run in {ws.display(ws.cwd)}",
            border_style="yellow",
        )

    rendered = "\n".join(f"{k}: {v}" for k, v in args.items()) or "(no arguments)"
    return Panel(rendered, title=tool.key, border_style="yellow")


class ApprovalPolicy:
    """Decides whether a tool call runs. The denylist is checked separately, in
    the sandbox, and is not affected by anything here -- including yolo."""

    def __init__(
        self,
        *,
        yolo: bool = False,
        auto_approve: list[str] | None = None,
        can_prompt: bool = True,
    ) -> None:
        self.yolo = yolo
        self.always: set[str] = set(auto_approve or [])
        self.can_prompt = can_prompt

    def needs_confirmation(self, tool: AgentTool) -> bool:
        return tool.mutating and not self.yolo and tool.key not in self.always

    def confirm(self, tool: AgentTool, args: dict[str, Any], ws: Workspace) -> bool:
        ui.console.print()
        ui.console.print(preview(tool, args, ws))

        # With no terminal there is nobody to ask, and silently approving would turn
        # --print into an unattended agent with write access. Refuse instead.
        if not self.can_prompt:
            ui.warn(f"{tool.key} needs confirmation, which is not possible without a terminal.")
            ui.dim("Re-run interactively, or pass --yolo to allow mutating tools.")
            return False

        choice = ui.select(f"Run {tool.key}?", [_YES, _NO, _ALWAYS])
        if choice == _ALWAYS:
            self.always.add(tool.key)
            return True
        return choice == _YES
