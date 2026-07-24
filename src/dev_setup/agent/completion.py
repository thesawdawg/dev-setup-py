from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

if TYPE_CHECKING:  # pragma: no cover
    from prompt_toolkit.complete import CompleteEvent

    from dev_setup.agent.registry import AgentTool


class SlashCompleter(Completer):
    """Completes `/` at the start of a line: session commands first, then the
    agent's tools.

    Scoped to the first word of a line that begins with `/`. Once there is a space
    the user is writing an argument or ordinary prose, and popping a menu over that
    is noise -- and a `/` mid-sentence (a path, a date) must never trigger it.
    """

    def __init__(self, commands: list[tuple[str, str]], tools: dict[str, AgentTool]) -> None:
        self.commands = commands
        self.tools = tools

    def _candidates(self) -> Iterable[tuple[str, str, str]]:
        for name, description in self.commands:
            yield name, description, "class:completion.command"
        for key, tool in sorted(self.tools.items()):
            marker = "! " if tool.mutating else ""
            yield f"/{key}", f"{marker}{tool.description}", "class:completion.tool"

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text or "\n" in text:
            return

        for name, description, style in self._candidates():
            if name.startswith(text):
                yield Completion(
                    name,
                    start_position=-len(text),
                    display=name,
                    display_meta=description[:70],
                    style=style,
                )
