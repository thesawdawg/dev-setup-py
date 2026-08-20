from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import questionary
from questionary import Style as QStyle
from rich import box
from rich.color import Color
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)

# ── palette ───────────────────────────────────────────────────────────────────
# A violet→cyan accent ramp over neutral grays. Everything visible keys off these
# constants so the theme lives in exactly one place.
VIOLET = "#7C3AED"
PURPLE = "#A78BFA"
CYAN = "#22D3EE"
GREEN = "#34D399"
AMBER = "#FBBF24"
RED = "#F87171"
GRAY = "#6B7280"
BORDER = "#4B4568"  # muted violet — boxes, rules, table frames

_STYLE = QStyle([
    ("qmark",       f"fg:{CYAN} bold"),
    ("question",    "bold"),
    ("answer",      f"fg:{PURPLE} bold"),
    ("pointer",     f"fg:{VIOLET} bold"),
    ("highlighted", f"fg:{PURPLE} bold"),
    ("selected",    f"fg:{CYAN}"),
    ("separator",   f"fg:{PURPLE}"),
    ("instruction", f"fg:{GRAY} italic"),
    ("check",       f"fg:{GREEN} bold"),
    ("disabled",    f"fg:{GRAY} italic"),
])

# Left-edge-only border — the chat-block look (a coloured gutter bar, no frame).
LEFT_BAR = box.Box(
    "    \n"
    "┃   \n"
    "┃   \n"
    "┃   \n"
    "┃   \n"
    "┃   \n"
    "┃   \n"
    "    \n"
)

# Rounded outer frame + header underline, no column dividers — the frame carries
# the structure without turning every row into a grid.
ROUNDED_OPEN = box.Box(
    "╭──╮\n"
    "│  │\n"
    "├──┤\n"
    "│  │\n"
    "├──┤\n"
    "├──┤\n"
    "│  │\n"
    "╰──╯\n"
)


def gradient(text: str, start: str = VIOLET, end: str = CYAN) -> Text:
    """Render `text` in bold with a per-character colour ramp from `start` to `end`."""
    c1 = Color.parse(start).triplet
    c2 = Color.parse(end).triplet
    out = Text()
    n = max(len(text) - 1, 1)
    for i, ch in enumerate(text):
        r = round(c1.red + (c2.red - c1.red) * i / n)
        g = round(c1.green + (c2.green - c1.green) * i / n)
        b = round(c1.blue + (c2.blue - c1.blue) * i / n)
        out.append(ch, style=f"bold #{r:02x}{g:02x}{b:02x}")
    return out


def info(msg: str) -> None:
    console.print(f"  [{CYAN} bold]❯[/]  {msg}")


def success(msg: str) -> None:
    console.print(f"  [{GREEN} bold]✔[/]  {msg}")


def warn(msg: str) -> None:
    console.print(f"  [{AMBER} bold]⚠[/]  {msg}")


def error(msg: str) -> None:
    console.print(f"  [{RED} bold]✖[/]  {msg}")


def dim(msg: str) -> None:
    console.print(f"  [{GRAY}]{msg}[/]")


def section(title: str) -> None:
    console.print()
    console.print(Panel(gradient(title), border_style=VIOLET, expand=False, padding=(0, 1)))
    console.print()


def heading(title: str, note: str = "") -> None:
    """A labelled rule: `── title ────────` with an optional dim annotation."""
    text = f"[bold {PURPLE}]{title}[/]"
    if note:
        text += f"  [{GRAY}]{note}[/]"
    console.print(Rule(text, style=BORDER, align="left"))


def divider() -> None:
    console.print(Rule(style=BORDER))


def print_banner() -> None:
    from dev_setup import __version__
    t = gradient("devstuff")
    t.append(f"  v{__version__}", style=GRAY)
    console.print()
    console.print(Panel(t, border_style=VIOLET, padding=(0, 2), expand=False))
    console.print()


def table(title: str = "", *, bordered: bool = True, **kwargs) -> Table:
    """The shared table look: rounded muted-violet frame, purple headers.

    `title` may contain markup and renders left-justified above the frame.
    `bordered=False` drops the frame for contexts (like --help) where a box
    would be chrome for its own sake. Extra kwargs pass through to `Table`.
    """
    return Table(
        box=ROUNDED_OPEN if bordered else None,
        border_style=BORDER,
        title=title or None,
        title_justify="left",
        header_style=f"bold {PURPLE}",
        padding=(0, 1),
        pad_edge=bordered,
        **kwargs,
    )


def gutter(renderable: RenderableType, style: str = VIOLET) -> Panel:
    """Wrap `renderable` in a panel with only a coloured left bar."""
    return Panel(renderable, box=LEFT_BAR, border_style=style, padding=(0, 1))


@contextmanager
def spinner(label: str) -> Generator[None, None, None]:
    with console.status(f"  [{GRAY}]{label}[/]", spinner="arc", spinner_style=PURPLE):
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


def select(
    prompt: str, choices: list, default: object | None = None, *, searchable: bool = False
) -> str:
    """Single-choice prompt. `choices` may be plain strings or questionary.Choice
    objects; `default` is the choice (or value) the cursor starts on. `searchable`
    lets the user type to filter the list — which turns off the j/k movement keys,
    since those would otherwise be swallowed as search text."""
    result = _ask(
        questionary.select(
            prompt,
            choices=choices,
            default=default,
            style=_STYLE,
            use_search_filter=searchable,
            use_jk_keys=not searchable,
        )
    )
    return result or ""


def checkbox(prompt: str, choices: list, **kwargs) -> list:
    result = _ask(questionary.checkbox(prompt, choices=choices, style=_STYLE, **kwargs))
    return result or []


def autocomplete(prompt: str, choices: list, **kwargs) -> str:
    """A filterable text prompt. As the user types, the list narrows to matching
    choices; Enter returns whatever is in the input buffer (a selected choice or
    the typed text). ``choices`` are plain strings; ``meta_information`` is a
    dict mapping choice → description shown for the highlighted row."""
    result = _ask(questionary.autocomplete(prompt, choices=choices, style=_STYLE, **kwargs))
    return result or ""


def password(prompt: str) -> str:
    result = _ask(questionary.password(prompt, style=_STYLE))
    return result or ""


def code_block(code: str, language: str = "bash") -> None:
    """Print a syntax-highlighted code panel."""
    console.print(
        Panel(
            Syntax(code, language, theme="monokai", line_numbers=False),
            border_style=BORDER,
            padding=(0, 1),
        )
    )
