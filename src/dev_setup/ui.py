from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

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


def confirm(prompt: str, default: bool = False) -> bool:
    result = questionary.confirm(prompt, default=default, style=_STYLE).ask()
    return bool(result)


def text_input(prompt: str, default: str = "", required: bool = False) -> str:
    while True:
        result = questionary.text(prompt, default=default, style=_STYLE).ask()
        val = (result or "").strip()
        if val or not required:
            return val
        error("This field is required.")


def select(prompt: str, choices: list[str]) -> str:
    result = questionary.select(prompt, choices=choices, style=_STYLE).ask()
    return result or ""


def checkbox(prompt: str, choices: list) -> list:
    result = questionary.checkbox(prompt, choices=choices, style=_STYLE).ask()
    return result or []
