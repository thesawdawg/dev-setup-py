from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import questionary
from questionary import Style as QStyle
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console(highlight=False)

_STYLE = QStyle([
    ("qmark",       "fg:#7C3AED bold"),
    ("question",    "bold"),
    ("answer",      "fg:#A78BFA bold"),
    ("pointer",     "fg:#7C3AED bold"),
    ("highlighted", "fg:#A78BFA bold"),
    ("selected",    "fg:#A78BFA"),
    ("separator",   "fg:#6B7280"),
    ("instruction", "fg:#6B7280 italic"),
    ("check",       "fg:#22C55E bold"),
])


def info(msg: str) -> None:
    console.print(f"  [cyan bold]❯[/]  {msg}")


def success(msg: str) -> None:
    console.print(f"  [green bold]✔[/]  {msg}")


def warn(msg: str) -> None:
    console.print(f"  [yellow bold]⚠[/]  {msg}")


def error(msg: str) -> None:
    console.print(f"  [red bold]✖[/]  {msg}")


def dim(msg: str) -> None:
    console.print(f"  [dim]{msg}[/]")


def section(title: str) -> None:
    console.print()
    console.print(
        Panel(f"[bold]{title}[/]", border_style="bright_magenta", expand=False, padding=(0, 1))
    )
    console.print()


def divider() -> None:
    console.print(Rule(style="dim"))


def print_banner() -> None:
    from dev_setup import __version__
    t = Text()
    t.append(" dev", style="bold bright_magenta")
    t.append("-", style="dim")
    t.append("setup", style="bold white")
    t.append(f"  v{__version__}", style="dim")
    console.print()
    console.print(Panel(t, border_style="bright_magenta", padding=(0, 2), expand=False))
    console.print()


@contextmanager
def spinner(label: str) -> Generator[None, None, None]:
    with console.status(f"  [dim]{label}[/]", spinner="dots"):
        yield


def _ask(question) -> object:
    """Run a questionary prompt via unsafe_ask() so Ctrl+C/Ctrl+D raise
    KeyboardInterrupt/EOFError instead of being swallowed into a None return
    (questionary's default .ask() catches KeyboardInterrupt and retries
    silently, which makes required prompts impossible to cancel). Click's
    top-level command dispatch already catches both and exits cleanly with
    "Aborted!", so letting them propagate is enough."""
    return question.unsafe_ask()


def confirm(prompt: str, default: bool = False) -> bool:
    result = _ask(questionary.confirm(prompt, default=default, style=_STYLE))
    return bool(result)


def text_input(prompt: str, default: str = "", required: bool = False) -> str:
    while True:
        result = _ask(questionary.text(prompt, default=default, style=_STYLE))
        val = (result or "").strip()
        if val or not required:
            return val
        error("This field is required.")


def select(prompt: str, choices: list[str]) -> str:
    result = _ask(questionary.select(prompt, choices=choices, style=_STYLE))
    return result or ""


def checkbox(prompt: str, choices: list, **kwargs) -> list:
    result = _ask(questionary.checkbox(prompt, choices=choices, style=_STYLE, **kwargs))
    return result or []


def password(prompt: str) -> str:
    result = _ask(questionary.password(prompt, style=_STYLE))
    return result or ""


def code_block(code: str, language: str = "bash") -> None:
    """Print a syntax-highlighted code panel."""
    from rich.syntax import Syntax
    console.print(
        Panel(
            Syntax(code, language, theme="monokai", line_numbers=False),
            border_style="dim",
            padding=(0, 1),
        )
    )
