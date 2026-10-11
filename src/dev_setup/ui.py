from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass

import questionary
from questionary import Style as QStyle
from rich import box
from rich.color import Color
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
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
    ("pointer",     f"fg:{CYAN} bold underline"),
    ("highlighted", f"fg:{CYAN} bold underline"),
    ("selected",    f"fg:{CYAN} bold"),
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


def section(title: str, note: str = "") -> None:
    """Banner panel for a command section. `note` renders dim inside the panel —
    plain text only: `title` goes through the gradient, which does not parse
    markup."""
    text = gradient(title)
    if note:
        text.append(f"  {note}", style=GRAY)
    console.print()
    console.print(Panel(text, border_style=VIOLET, expand=False, padding=(0, 1)))
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


def _pad_choices(choices: list) -> list:
    """Stretch string titles to the row width so the `highlighted` background
    reads as a full-width bar under the cursor rather than a patch behind the
    text. Bare strings become Choices so the returned value stays unpadded."""
    width = console.width - 6
    out: list = []
    for c in choices:
        if isinstance(c, str):
            out.append(questionary.Choice(title=c.ljust(width), value=c))
        elif isinstance(c, questionary.Choice) and isinstance(c.title, str):
            c.title = c.title.ljust(width)
            out.append(c)
        else:
            out.append(c)
    return out


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
            choices=_pad_choices(choices),
            default=default,
            style=_STYLE,
            use_search_filter=searchable,
            use_jk_keys=not searchable,
        )
    )
    return result or ""


def checkbox(prompt: str, choices: list, **kwargs) -> list:
    result = _ask(
        questionary.checkbox(prompt, choices=_pad_choices(choices), style=_STYLE, **kwargs)
    )
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


# ── task view: phase checklist + progress bar + live output tail ───────────────
#
# One live region drives an install/remove/update: a phase-fraction progress bar
# over a checklist where finished steps persist as ✔, plus a tail box showing the
# last few lines of real subprocess output. The view collapses to its checklist
# on success and keeps the tail on failure (that's the error context).
#
# Subprocess output reaches the tail through `_run` in generic.py, which reads
# `current_view()` — a process-wide hook like the verbose level, so installers
# never thread the view through call signatures.

_ACTIVE_VIEW: TaskView | None = None


def current_view() -> TaskView | None:
    return _ACTIVE_VIEW


_PHASE_SPINNER = "◐◓◑◒"
_ERROR_TAIL_LINES = 30


class TaskView:
    """The live region. Not constructed directly — use `task_view`."""

    def __init__(self, title: str, total: int, tail_lines: int = 5):
        self.title = title
        self.total = max(total, 1)
        self._phases: list[list] = []  # [label, state] state: done|current|failed
        self._tail: deque[str] = deque(maxlen=tail_lines)
        self._errors: deque[str] = deque(maxlen=_ERROR_TAIL_LINES)
        self._partial = False  # last tail entry is an unfinished line
        self._live: Live | None = None

    # -- driving the view -----------------------------------------------------

    @contextmanager
    def phase(self, label: str) -> Generator[None, None, None]:
        for p in self._phases:
            if p[1] == "current":
                p[1] = "done"
        self._phases.append([label, "current"])
        self._repaint()
        try:
            yield
        except BaseException:
            self._phases[-1][1] = "failed"
            self._repaint()
            raise
        self._phases[-1][1] = "done"
        self._repaint()

    def feed(self, line: str, *, partial: bool = False) -> None:
        """One line of subprocess output → the tail (and the failure buffer).

        `partial=True` marks an unfinished line — output still in progress or a
        prompt like `[sudo] password for …` that ends without a newline. It
        updates the last tail entry in place rather than appending, so a
        progress bar doesn't flood the tail; its final form arrives as a
        normal feed once the newline shows up.
        """
        if self._partial and self._tail:
            self._tail[-1] = line
        else:
            self._tail.append(line)
        self._partial = partial
        if not partial and line.strip():
            self._errors.append(line)
        self._repaint()

    def error_output(self) -> str:
        return "\n".join(self._errors).strip()

    # -- rendering ------------------------------------------------------------

    def _repaint(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    def _render(self, *, final: bool = False, failed: bool = False) -> Group:
        done = sum(1 for _, s in self._phases if s == "done")
        bar_style = RED if failed else (GREEN if final else VIOLET)
        bar = ProgressBar(
            total=self.total,
            completed=min(done, self.total),
            width=24,
            complete_style=bar_style,
            finished_style=bar_style,
        )
        frame = _PHASE_SPINNER[int(time.monotonic() * 6) % len(_PHASE_SPINNER)]

        lines: list[RenderableType] = [
            Columns(
                [
                    Text(f"  {self.title}  ", style="bold"),
                    bar,
                    Text(f" {done}/{self.total}", style=GRAY),
                ],
                padding=0,
                expand=False,
            )
        ]
        for label, state in self._phases:
            if state == "done":
                lines.append(Text(f"  ✔  {label}", style=GREEN))
            elif state == "failed":
                lines.append(Text(f"  ✖  {label}", style=f"{RED} bold"))
            else:
                lines.append(Text.assemble((f"  {frame}  ", PURPLE), (label, "")))
        remaining = self.total - len(self._phases)
        for _ in range(max(remaining, 0)):
            lines.append(Text("  ○", style=GRAY))
        if self._tail and not (final and not failed):
            lines.append(Text(""))
            for line in self._tail:
                # from_ansi parses the subprocess's own escape codes — raw
                # ANSI in a Text would reach the terminal mid-repaint and
                # corrupt the live region.
                tail = Text.from_ansi(f"  ┃ {line}")
                tail.stylize(GRAY)
                lines.append(tail)
        return Group(*lines)

    def _finish(self, failed: bool) -> None:
        if self._live is None:
            return
        # Success collapses to the checklist; failure keeps the tail as context.
        self._live.update(self._render(final=True, failed=failed), refresh=True)
        self._live.stop()
        self._live = None


class _NullView:
    """Verbose/non-TTY stand-in: phases degrade to the old spinner/log behaviour."""

    @contextmanager
    def phase(self, label: str) -> Generator[None, None, None]:
        from dev_setup import verbose
        with verbose.step(label):
            yield

    def feed(self, line: str, *, partial: bool = False) -> None:
        pass

    def error_output(self) -> str:
        return ""


@contextmanager
def task_view(
    title: str, *, total: int = 1, tail_lines: int = 5
) -> Generator[TaskView | _NullView, None, None]:
    """Live install progress. Degrades to verbose.step in verbose mode/non-TTY."""
    global _ACTIVE_VIEW
    from dev_setup import verbose

    if verbose.enabled() or not console.is_terminal:
        yield _NullView()
        return

    view = TaskView(title, total, tail_lines)
    view._live = Live(
        view._render(), console=console, refresh_per_second=10, transient=True
    )
    _ACTIVE_VIEW = view
    failed = False
    try:
        with view._live:
            try:
                yield view
            except BaseException:
                failed = True
                raise
    finally:
        _ACTIVE_VIEW = None
        view._finish(failed)


@contextmanager
def phase(label: str) -> Generator[None, None, None]:
    """Advance the active task view (or fall back to verbose.step without one)."""
    view = _ACTIVE_VIEW
    if view is None:
        view = _NullView()
    with view.phase(label):
        yield


# ── plan preview ───────────────────────────────────────────────────────────────

# (kind, label, detail): kind "cmd" shows `$ detail`, "script" shows detail as a
# syntax block, "note" is a dim annotation line.
PlanItem = tuple[str, str, str]


def plan_panel(title: str, items: list[PlanItem], note: str = "") -> None:
    """Render the 'here is what will run' preview before a state-changing op."""
    parts: list[RenderableType] = []
    for kind, label, detail in items:
        if kind == "script":
            parts.append(Text(f"  {label}:", style=f"bold {PURPLE}"))
            parts.append(Syntax(detail, "bash", theme="monokai", line_numbers=False))
        elif kind == "note":
            parts.append(Text(f"  ↳ {detail}", style=GRAY))
        else:
            parts.append(
                Text.assemble(("  $ ", f"{CYAN} bold"), (detail, ""))
            )
    if note:
        parts.append(Text(""))
        parts.append(Text(f"  {note}", style=f"{AMBER}"))
    console.print(
        Panel(Group(*parts), border_style=BORDER, padding=(0, 1), expand=True)
    )


def batch_plan_panel(rows: list[tuple[str, str]], action: str) -> None:
    """Compact one-line-per-tool plan for multi-package runs."""
    tbl = table(title=f"[bold]{action} plan[/]")
    tbl.add_column("Package", style="bold", no_wrap=True)
    tbl.add_column("Will run", ratio=1, overflow="fold")
    for name, detail in rows:
        # Text, not a markup string: a plan is the exact command, and `[lock]` in
        # `ansible-lint[lock]` is text to show, not a style tag for Rich to delete.
        tbl.add_row(Text(name), Text(detail))
    console.print(tbl)
    console.print()


# ── run summary ────────────────────────────────────────────────────────────────

_SUMMARY_ICON = {"ok": (GREEN, "●"), "skip": (GRAY, "○"), "fail": (RED, "✖")}


def summary(results: list[tuple[str, str, str]], title: str = "Summary") -> None:
    """End-of-run report. `results` rows are (label, status, detail) where
    status is "ok" | "skip" | "fail" and detail is a version or error line."""
    n_ok = sum(1 for _, s, _ in results if s == "ok")
    n_fail = sum(1 for _, s, _ in results if s == "fail")
    tbl = table(
        title=f"[bold]{title}[/]  [{GRAY}]{n_ok} ok · {n_fail} failed[/]"
    )
    tbl.add_column("", width=2, justify="center")
    tbl.add_column("Package", style="bold", no_wrap=True)
    tbl.add_column("Result", ratio=1, overflow="fold")
    for label, status, detail in results:
        color, icon = _SUMMARY_ICON.get(status, _SUMMARY_ICON["skip"])
        # `detail` is an installer's error line: arbitrary text, so never parsed as markup
        # (`[/usr/bin/x]` is a closing tag to Rich and raises MarkupError).
        tbl.add_row(f"[{color}]{icon}[/]", Text(label), Text(detail))
    console.print()
    console.print(tbl)
    console.print()


# ── wizard framework ───────────────────────────────────────────────────────────
#
# A Wizard runs a list of Steps with a "Step i/N" indicator, back navigation,
# and a review screen where any field can be jumped to and re-edited. Steps
# return a value, or the BACK sentinel to move to the previous step.
#
# Back is signalled two ways: a "← Back" sentinel choice prepended to selects,
# and an Escape key binding on text inputs (questionary forwards key_bindings
# to PromptSession). Skipping a step backwards keeps the later answers, which
# each step's `ask` uses as its default — re-traversal re-asks with context.

BACK = object()


def _back_key_bindings():
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    # Ctrl+B, not Escape — a bare Escape binding competes with the escape
    # sequences arrow keys send (they all start with ESC).
    @kb.add("c-b")
    def _back(event) -> None:
        event.app.exit(result=BACK)

    return kb


def select_back(prompt: str, choices: list, default: object | None = None) -> object:
    """select() with a '← Back' entry appended; returns BACK if picked.

    Appended, not prepended — as the first choice it would be what a bare Enter
    lands on, making the first step of every wizard an accidental abort.
    """
    entries = list(choices) + [questionary.Choice(title="← Back", value=BACK)]
    result = select(prompt, entries, default=default)
    return result


def text_input_back(prompt: str, default: str = "", required: bool = False) -> object:
    """text_input() where Escape returns BACK."""
    while True:
        result = _ask(
            questionary.text(
                prompt,
                default=default,
                style=_STYLE,
                key_bindings=_back_key_bindings(),
                instruction="(Ctrl+B: back)",
            )
        )
        if result is BACK:
            return BACK
        val = (result or "").strip()
        if val or not required:
            return val
        error("This field is required.")


@dataclass
class Step:
    """One wizard question. `ask(answers)` returns a value or BACK.

    `when(answers)` skips the step unless it returns true; `show(value)` formats
    the answer on the review screen (defaults to str, scripts truncated).
    """

    key: str
    label: str
    ask: Callable[[dict], object]
    when: Callable[[dict], bool] | None = None
    show: Callable[[object], str] = lambda v: str(v)


class Wizard:
    """Runs steps linearly with back-nav, ending in an editable review screen."""

    def __init__(self, title: str):
        self.title = title

    def run(self, steps: list[Step]) -> dict | None:
        answers: dict = {}
        applicable = [s for s in steps if s.when is None or s.when(answers)]

        idx = 0
        while idx < len(applicable):
            step = applicable[idx]
            console.print()
            dim(f"  {self.title} · step {idx + 1}/{len(applicable)} — {step.label}")
            result = step.ask(answers)
            if result is BACK:
                if idx == 0:
                    return None  # backed out of the first step = abort
                idx -= 1
                continue
            answers[step.key] = result
            idx += 1
            # Recompute applicability — an edited earlier answer may change it.
            applicable = [s for s in steps if s.when is None or s.when(answers)]

        return self._review(steps, answers)

    def _review(self, steps: list[Step], answers: dict) -> dict | None:
        while True:
            # Recomputed each pass: editing an answer (e.g. the package type) can
            # change which steps apply.
            applicable = [s for s in steps if s.when is None or s.when(answers)]
            console.print()
            heading("Review")
            for step in applicable:
                # Falsy answers aren't persisted to the catalog — hide them so
                # the review shows exactly what will be saved.
                if answers.get(step.key):
                    console.print(
                        f"  [{PURPLE}]{step.label:<18}[/] {step.show(answers[step.key])}"
                    )
            console.print()
            pick = select(
                "Edit a field, or confirm:",
                [questionary.Choice("✔ Confirm", value="__done__")]
                + [
                    questionary.Choice(f"edit {s.label}", value=s.key)
                    for s in applicable
                ]
                + [questionary.Choice("✖ Abort", value="__abort__")],
            )
            if pick == "__abort__":
                return None
            if pick in ("__done__", ""):
                # An edit may have made steps applicable that were never asked —
                # collect their answers before letting the wizard finish.
                missing = [s for s in applicable if s.key not in answers]
                if not missing:
                    return answers
                for s in missing:
                    result = s.ask(answers)
                    if result is not BACK:
                        answers[s.key] = result
                continue
            step = next(s for s in applicable if s.key == pick)
            result = step.ask(answers)
            if result is not BACK:
                answers[step.key] = result
