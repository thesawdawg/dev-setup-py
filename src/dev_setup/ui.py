from __future__ import annotations

import os
import sys
import time
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass
from threading import Lock

import questionary
from prompt_toolkit.styles import StyleTransformation
from pygments.token import Token
from questionary import Style as QStyle
from rich import box
from rich.color import Color
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.style import Style
from rich.syntax import ANSISyntaxTheme, Syntax
from rich.table import Table
from rich.text import Text

from dev_setup.theme import ACCENT, CAUTION, DESTRUCTIVE, PRIMARY, SECONDARY, SUCCESS


def _plain_requested() -> bool:
    return os.environ.get("DEVSTUFF_PLAIN") == "1" or os.environ.get("TERM", "").lower() == "dumb"


def _isatty(file) -> bool:
    try:
        return file.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _plain() -> bool:
    # FORCE_COLOR must not turn a pipe into an animated terminal.
    return _plain_requested() or not _isatty(console.file) or not console.is_terminal


console = Console(
    highlight=False,
    color_system=None if _plain_requested() or not _isatty(sys.stdout) else "auto",
)

# Compatibility aliases for the styles referenced by command handlers and wizards.
VIOLET = PURPLE = CYAN = ACCENT
GREEN = SUCCESS
AMBER = CAUTION
RED = DESTRUCTIVE
GRAY = BORDER = SECONDARY

_STYLE = QStyle([
    ("qmark",       f"fg:{ACCENT} bold"),
    ("question",    f"fg:{PRIMARY} bold"),
    ("text",        f"fg:{PRIMARY}"),
    ("answer",      f"fg:{ACCENT} bold"),
    ("pointer",     f"fg:{ACCENT} bold"),
    ("highlighted", f"fg:{ACCENT} bold underline"),
    ("selected",    f"fg:{ACCENT} bold"),
    ("separator",   f"fg:{SECONDARY}"),
    ("instruction", f"fg:{SECONDARY}"),
    ("check",       f"fg:{SUCCESS} bold"),
    ("disabled",    f"fg:{SECONDARY}"),
    ("search",      f"fg:{ACCENT} underline"),
    ("description", f"fg:{SECONDARY}"),
    ("validation-toolbar", f"fg:{DESTRUCTIVE} bold"),
])


class _PromptStyle(StyleTransformation):
    """Strip backgrounds (including reverse-video defaults in toolkit widgets)."""

    def __init__(self, no_color: bool):
        self.no_color = no_color

    def transform_attrs(self, attrs):
        return attrs._replace(
            color="" if self.no_color else attrs.color, bgcolor="", reverse=False
        )


_SYNTAX_THEME = ANSISyntaxTheme({
    tuple(Token.Text): Style(color=PRIMARY),
    tuple(Token.Comment): Style(color=SECONDARY),
    tuple(Token.Keyword): Style(color=ACCENT, bold=True),
    tuple(Token.Name): Style(color=PRIMARY),
    tuple(Token.Literal): Style(color=SUCCESS),
    tuple(Token.Operator): Style(color=CAUTION),
})


def _syntax(code: str, language: str) -> Syntax:
    return Syntax(code, language, theme=_SYNTAX_THEME, word_wrap=True)

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


def gradient(text: str, start: str = ACCENT, end: str = PRIMARY) -> Text:
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
    console.print(f"  [{ACCENT} bold]{'>' if _plain() else '❯'}[/]  {msg}")


def success(msg: str) -> None:
    console.print(f"  [{SUCCESS} bold]{'OK' if _plain() else '✔'}[/]  {msg}")


def warn(msg: str) -> None:
    console.print(f"  [{CAUTION} bold]{'Warning:' if _plain() else '⚠'}[/]  {msg}")


def error(msg: str) -> None:
    console.print(f"  [{DESTRUCTIVE} bold]{'Error:' if _plain() else '✖'}[/]  {msg}")


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
    console.print(Panel(text, box=box.ASCII if _plain() else box.ROUNDED,
                        border_style=BORDER, expand=False, padding=(0, 1)))
    console.print()


def heading(title: str, note: str = "") -> None:
    """A labelled rule: `── title ────────` with an optional dim annotation."""
    text = f"[bold {PURPLE}]{title}[/]"
    if note:
        text += f"  [{GRAY}]{note}[/]"
    console.print(Rule(text, characters="-" if _plain() else "─", style=BORDER, align="left"))


def divider() -> None:
    console.print(Rule(characters="-" if _plain() else "─", style=BORDER))


def print_banner() -> None:
    from dev_setup import __version__
    t = gradient("devstuff")
    t.append(f"  v{__version__}", style=GRAY)
    console.print()
    console.print(Panel(t, box=box.ASCII if _plain() else box.ROUNDED,
                        border_style=BORDER, padding=(0, 2), expand=False))
    console.print()


def table(title: str = "", *, bordered: bool = True, **kwargs) -> Table:
    """The shared table look: open frame and accent headers.

    `title` may contain markup and renders left-justified above the frame.
    `bordered=False` drops the frame for contexts (like --help) where a box
    would be chrome for its own sake. Extra kwargs pass through to `Table`.
    """
    return Table(
        box=(box.ASCII if _plain_requested() else ROUNDED_OPEN) if bordered else None,
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
    return Panel(renderable, box=box.ASCII if _plain() else LEFT_BAR,
                 border_style=style, padding=(0, 1))


@contextmanager
def spinner(label: str) -> Generator[None, None, None]:
    text = Text(f"  {label}", style=SECONDARY)
    if _plain():
        console.print(text)
        yield
        return
    with console.status(text, spinner="arc", spinner_style=ACCENT):
        yield


def _ask(question, *, back: Callable[[], object] | None = None) -> object:
    """Run a questionary prompt via unsafe_ask() so Ctrl+C/Ctrl+D raise
    KeyboardInterrupt/EOFError instead of being swallowed into a None return
    (questionary's default .ask() catches KeyboardInterrupt and retries
    silently, which makes required prompts impossible to cancel). Click's
    top-level command dispatch already catches both and exits cleanly with
    "Aborted!", so letting them propagate is enough."""
    app = getattr(question, "application", None)
    if app is not None:
        no_color = "NO_COLOR" in os.environ or _plain() or console.no_color
        app.style_transformation = _PromptStyle(no_color)
        if back is not None:
            @app.key_bindings.add("c-b", eager=True)
            def back_key(event) -> None:
                try:
                    answer = back()
                except KeyboardInterrupt:
                    event.app.exit(exception=KeyboardInterrupt)
                else:
                    event.app.exit(result=answer)

    view = current_view()
    if view is None or view._live is None:
        return question.unsafe_ask()
    # Give the prompt exclusive ownership. On abort let task_view unwind;
    # there is no reason to restart a display just to stop it again.
    view._live.stop()
    answer = question.unsafe_ask()
    view._live.start(refresh=True)
    return answer


def _static_prompt() -> bool:
    if _plain_requested():
        return True
    return sys.stdin.readable() and (_plain() or not _isatty(sys.stdin))


def _plain_input(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return console.input(f"{prompt}{suffix} ", markup=False, emoji=False) or default


def confirm(prompt: str, default: bool = False) -> bool:
    if _static_prompt():
        while True:
            choices = "Y/n" if default else "y/N"
            answer = _plain_input(f"{prompt} [{choices}]").strip().lower()
            if not answer:
                return default
            if answer in {"y", "yes", "n", "no"}:
                return answer in {"y", "yes"}
    result = _ask(questionary.confirm(prompt, default=default, style=_STYLE))
    return bool(result)


def text_input(prompt: str, default: str = "", required: bool = False) -> str:
    while True:
        result = (
            _plain_input(prompt, default) if _static_prompt()
            else _ask(questionary.text(prompt, default=default, style=_STYLE))
        )
        val = (result or "").strip()
        if val or not required:
            return val
        error("This field is required.")


def _pad_choices(choices: list) -> list:
    """Keep the existing row alignment without mutating reusable choices."""
    width = console.width - 6
    out: list = []
    for c in choices:
        if isinstance(c, str):
            out.append(questionary.Choice(title=c.ljust(width), value=c))
        elif isinstance(c, questionary.Separator):
            out.append(c)
        elif isinstance(c, questionary.Choice) and isinstance(c.title, str):
            c = copy(c)
            c.title = c.title.ljust(width)
            out.append(c)
        else:
            out.append(c)
    return out


def _picker_help(*, multiple: bool = False, searchable: bool = False, back: bool = False) -> str:
    keys = ["Up/Down move"]
    if multiple:
        keys.append("Space toggle")
    keys += ["Enter confirm", "Ctrl+B back" if back else "Ctrl+B cancel"]
    if searchable:
        keys.append("type to filter")
    return " | ".join(keys)


def _plain_pick(prompt: str, choices: list, default=None, *, multiple: bool = False,
                validate=None):
    """Numbered, line-oriented counterpart to the terminal picker."""
    entries = []
    defaults = []
    default = getattr(default, "value", default)
    for item in choices:
        if isinstance(item, questionary.Separator):
            console.print(Text(str(item.title)))
            continue
        choice = (
            item if isinstance(item, questionary.Choice)
            else questionary.Choice(**item) if isinstance(item, dict)
            else questionary.Choice(item)
        )
        title = choice.title
        if not isinstance(title, str):
            title = "".join(fragment[1] for fragment in title)
        title = title.strip()
        if choice.disabled:
            console.print(Text(f"  - {title} (unavailable: {choice.disabled})"))
            continue
        entries.append(choice.value)
        index = str(len(entries))
        checked = choice.value == default or (multiple and choice.checked)
        if checked:
            defaults.append(index)
        marker = ("[x] " if checked else "[ ] ") if multiple else ""
        console.print(Text(f"  {index}. {marker}{title}"))
    if not multiple and not defaults and entries:
        defaults = ["1"]
    help_text = "numbers separated by spaces" if multiple else "number"
    while entries:
        cancel_label = "back" if BACK in entries else "cancel"
        answer = _plain_input(f"{prompt} ({help_text}, 0 to {cancel_label})", " ".join(defaults))
        if answer.strip() == "0":
            if multiple:
                return []
            if BACK in entries:
                return BACK
            raise KeyboardInterrupt
        try:
            indices = {int(number) for number in answer.split()}
        except ValueError:
            indices = {0}
        if not indices <= set(range(1, len(entries) + 1)) or (not multiple and len(indices) != 1):
            console.print("Enter a number from the list, or 0 to cancel.")
            continue
        selected = [value for i, value in enumerate(entries, 1) if i in indices]
        if multiple:
            verdict = True if validate is None else validate(selected)
            if verdict is not True:
                console.print(Text(verdict if isinstance(verdict, str) else "Invalid selection."))
                continue
            return selected
        return selected[0]
    return [] if multiple else ""


def select(
    prompt: str, choices: list, default: object | None = None, *, searchable: bool = False
) -> str:
    """Single-choice prompt. `choices` may be plain strings or questionary.Choice
    objects; `default` is the choice (or value) the cursor starts on. `searchable`
    lets the user type to filter the list — which turns off the j/k movement keys,
    since those would otherwise be swallowed as search text."""
    if _static_prompt():
        return _plain_pick(prompt, choices, default)
    has_back = any(getattr(c, "value", None) is BACK for c in choices)
    result = _ask(
        questionary.select(
            prompt,
            choices=_pad_choices(choices),
            default=default,
            style=_STYLE,
            pointer=">",
            instruction=_picker_help(searchable=searchable, back=has_back),
            use_search_filter=searchable,
            use_jk_keys=not searchable,
        ),
        back=(lambda: BACK) if has_back else _cancel_prompt,
    )
    return result or ""


def _cancel_prompt() -> None:
    raise KeyboardInterrupt


def checkbox(prompt: str, choices: list, **kwargs) -> list:
    if _static_prompt():
        return _plain_pick(prompt, choices, kwargs.get("default"), multiple=True,
                           validate=kwargs.get("validate"))
    searchable = bool(kwargs.get("use_search_filter"))
    if searchable:
        kwargs.setdefault("use_jk_keys", False)
    help_text = _picker_help(multiple=True, searchable=searchable)
    if kwargs.get("instruction"):
        # Preserve caller-specific context (e.g. why rows are pre-checked).
        console.print(Text(help_text, style=SECONDARY))
    else:
        kwargs["instruction"] = help_text
    kwargs.setdefault("pointer", ">")
    result = _ask(
        questionary.checkbox(prompt, choices=_pad_choices(choices), style=_STYLE, **kwargs),
        back=lambda: None,
    )
    return result or []


def autocomplete(prompt: str, choices: list, **kwargs) -> str:
    """A filterable text prompt. As the user types, the list narrows to matching
    choices; Enter returns whatever is in the input buffer (a selected choice or
    the typed text). ``choices`` are plain strings; ``meta_information`` is a
    dict mapping choice → description shown for the highlighted row."""
    if _static_prompt():
        return _plain_input(prompt, kwargs.get("default", ""))
    result = _ask(questionary.autocomplete(prompt, choices=choices, style=_STYLE, **kwargs))
    return result or ""


def password(prompt: str) -> str:
    if _static_prompt():
        return console.input(f"{prompt} ", markup=False, emoji=False, password=True)
    result = _ask(questionary.password(prompt, style=_STYLE))
    return result or ""


def code_block(code: str, language: str = "bash") -> None:
    """Print a syntax-highlighted code panel."""
    console.print(
        Panel(
            _syntax(code, language),
            box=box.ASCII if _plain() else box.ROUNDED,
            border_style=BORDER,
            padding=(0, 1),
        )
    )


# ── task view: phase checklist + live output tail ─────────────────────────────
#
# Subprocess output reaches the tail through `_run` in generic.py, which reads
# `current_view()` — a process-wide hook like the verbose level, so installers
# never thread the view through call signatures.

_ACTIVE_VIEW: TaskView | None = None


def current_view() -> TaskView | None:
    return _ACTIVE_VIEW


_PHASE_SPINNER = "◐◓◑◒"
_ERROR_TAIL_LINES = 30


def _duration(seconds: int) -> str:
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _is_cancelled(exc: BaseException | None) -> bool:
    return isinstance(exc, (KeyboardInterrupt, EOFError))


class TaskView:
    """Phase and output state. Use `task_view` to attach a presentation."""

    def __init__(self, title: str, total: int, tail_lines: int = 5, *, log_path: str = ""):
        self.title = title
        self.total = max(total, 1)
        self._log_path = log_path
        self._phases: list[list[str]] = []
        self._tail: deque[str] = deque(maxlen=tail_lines)
        self._errors: deque[str] = deque(maxlen=_ERROR_TAIL_LINES)
        self._partial = False
        self._live: Live | None = None
        self._state_lock = Lock()
        self._started = self._phase_started = self._last_feed = time.monotonic()
        self._activity = ""

    # -- driving the view -----------------------------------------------------

    @contextmanager
    def phase(self, label: str) -> Generator[None, None, None]:
        phase = [label, "current"]
        with self._state_lock:
            for previous in self._phases:
                if previous[1] == "current":
                    previous[1] = "done"
            self._phases.append(phase)
            self._phase_started = self._last_feed = time.monotonic()
            self._activity = ""
            self._partial = False
        try:
            yield
        except BaseException as exc:
            with self._state_lock:
                phase[1] = "cancelled" if _is_cancelled(exc) else "failed"
            raise
        else:
            with self._state_lock:
                phase[1] = "done"

    def set_activity(self, label: str) -> None:
        """Show an externally reported activity (e.g. draining); empty clears it."""
        with self._state_lock:
            self._activity = label

    def feed(self, line: str, *, partial: bool = False) -> None:
        """One line of subprocess output → the tail (and the failure buffer).

        `partial=True` marks an unfinished line — output still in progress or a
        prompt like `[sudo] password for …` that ends without a newline. It
        updates the last tail entry in place rather than appending, so a
        progress bar doesn't flood the tail; its final form arrives as a
        normal feed once the newline shows up.
        """
        with self._state_lock:
            if self._partial and self._tail:
                self._tail[-1] = line
            else:
                self._tail.append(line)
            self._partial = partial
            if not partial and line.strip():
                self._errors.append(line)
            self._last_feed = time.monotonic()

    def error_output(self) -> str:
        with self._state_lock:
            return "\n".join(self._errors).strip()

    # -- rendering ------------------------------------------------------------

    def _render(self, *, final: bool = False, failed: bool = False,
                cancelled: bool = False, plain: bool = False) -> Group:
        # Rich calls this while holding its own lock. Snapshot state briefly;
        # no code holding our lock can call back into Live or the Console.
        with self._state_lock:
            now = time.monotonic()
            phases = [tuple(phase) for phase in self._phases]
            tail = tuple(self._tail)
            elapsed = int(now - self._started)
            phase_elapsed = int(now - self._phase_started)
            idle = int(now - self._last_feed)
            activity = self._activity

        done = sum(1 for _, state in phases if state == "done")
        status, color = (
            ("Cancelled", CAUTION) if cancelled else ("Failed", DESTRUCTIVE) if failed
            else ("Done", SUCCESS) if final else ("In progress", ACCENT)
        )
        total = len(phases) if final and not (failed or cancelled) else max(self.total, len(phases))
        count = f" | {done}/{total} phases" if total else ""
        lines = [
            Text(self.title, style=f"{PRIMARY} bold", overflow="fold"),
            Text(f"{status} | {_duration(elapsed)} elapsed{count}", style=f"{color} bold"),
        ]
        frame = ">" if plain else _PHASE_SPINNER[int(now * 6) % len(_PHASE_SPINNER)]
        shown_phases = phases if final else phases[-5:]
        if len(shown_phases) < len(phases):
            lines.append(Text("  Earlier phases are listed in the result.", style=SECONDARY))
        for label, state in shown_phases:
            if state == "done":
                lines.append(Text(f"  {'OK' if plain else '✔'} {label}", style=SUCCESS,
                                  overflow="fold"))
            elif state in ("failed", "cancelled"):
                phase_color = DESTRUCTIVE if state == "failed" else CAUTION
                lines.append(Text(f"  {state.capitalize()}: {label}", style=phase_color,
                                  overflow="fold"))
            else:
                lines.append(Text(f"  {frame} {label} ({_duration(phase_elapsed)})",
                                  style=PRIMARY, overflow="fold"))
        if not final:
            if activity:
                lines.append(Text(f"Activity: {activity}", style=PRIMARY, overflow="fold"))
            if idle >= 30:
                lines.append(Text(f"No new output for {idle}s; this does not indicate failure.",
                                  style=CAUTION))
        if tail:
            # Parse child ANSI but don't adopt its styles (which can paint a
            # background). Keep the full recent tail for a failed/cancelled result.
            shown = tail if final and (failed or cancelled) else tail[-1:]
            lines.append(Text("Last output:", style=f"{SECONDARY} bold"))
            lines.extend(Text(f"  {Text.from_ansi(line).plain}", style=SECONDARY,
                              no_wrap=not final, overflow="fold" if final else "ellipsis")
                         for line in shown)
        elif not final:
            lines.append(Text("Last output: (none yet)", style=SECONDARY))
        if self._log_path:
            lines.append(Text(f"Log: {self._log_path}", style=SECONDARY, overflow="fold"))
        if not final:
            lines.append(Text("Ctrl+C cancels. Partial changes are not rolled back.", style=PRIMARY))
        elif failed or cancelled:
            next_step = (
                "Review the log before retrying" if self._log_path
                else "Check what completed before retrying"
            )
            lines.append(Text(f"{next_step}; partial changes may remain.", style=PRIMARY))
        return Group(*lines)


class _NullView:
    """Track static results without switching subprocesses into live capture."""

    def __init__(self, model: TaskView | None = None):
        self._model = model

    @contextmanager
    def phase(self, label: str) -> Generator[None, None, None]:
        from dev_setup import verbose
        if self._model is None:
            with verbose.step(label):
                yield
        else:
            with self._model.phase(label), verbose.step(label):
                yield

    def set_activity(self, label: str) -> None:
        if self._model is not None:
            self._model.set_activity(label)

    def feed(self, line: str, *, partial: bool = False) -> None:
        if self._model is not None:
            self._model.feed(line, partial=partial)

    def error_output(self) -> str:
        return self._model.error_output() if self._model is not None else ""


_STATIC_VIEW: _NullView | None = None


@contextmanager
def task_view(
    title: str, *, total: int = 1, tail_lines: int = 5, log_path: str | None = None,
    force_plain: bool = False,
) -> Generator[TaskView | _NullView, None, None]:
    """Render live progress when possible and leave a durable result afterward.

    `force_plain` disables the live view even on a real terminal — used when a
    child owns the tty (terminal passthrough), where a Live would fight it."""
    global _ACTIVE_VIEW, _STATIC_VIEW
    from dev_setup import generic, verbose

    is_verbose = verbose.enabled()
    plain = force_plain or _plain()
    view = TaskView(title, total, tail_lines,
                    log_path=str(generic.run_log_path()) if log_path is None else log_path)
    previous_live, previous_static = _ACTIVE_VIEW, _STATIC_VIEW
    if plain or is_verbose:
        presented = _STATIC_VIEW = _NullView(view)
        _ACTIVE_VIEW = None
    else:
        # A callback, not a baked Group: Rich invokes it even without feed().
        view._live = Live(console=console, get_renderable=view._render,
                          refresh_per_second=4, transient=True)
        presented = _ACTIVE_VIEW = view
        _STATIC_VIEW = None

    failure = None
    try:
        if view._live is not None:
            view._live.start(refresh=True)
        elif not is_verbose:
            console.print(view._render(plain=True))
        yield presented
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _ACTIVE_VIEW, _STATIC_VIEW = previous_live, previous_static
        try:
            if view._live is not None:
                view._live.stop()
                view._live = None
            cancelled = _is_cancelled(failure)
            failed = failure is not None and not cancelled
            if is_verbose:
                result = "Cancelled" if cancelled else "Failed" if failed else "Done"
                log = f" | Log: {view._log_path}" if view._log_path and failure else ""
                verbose.log(f"{title}: {result}{log}")
            else:
                console.print(view._render(final=True, failed=failed, cancelled=cancelled,
                                           plain=plain))
        except Exception as render_error:
            if failure is None:
                raise
            failure.add_note(f"Could not finish the progress display: {render_error}")


@contextmanager
def phase(label: str) -> Generator[None, None, None]:
    """Advance the active task view (or fall back to verbose.step without one).

    The phase boundary is always recorded in the action log — a stable marker,
    not the label: labels interpolate argv fragments (URLs, package names) and
    those must not persist. Ordering against child-exit metadata is enough to
    correlate. In machine mode the phase is fully silent — no spinner, no
    line, regardless of what stderr or TERM happen to be — so nothing can
    leak onto a JSON/eval stream."""
    from dev_setup import runner

    runner.note("phase started")
    if runner.is_machine_mode():
        yield
        return
    view = _ACTIVE_VIEW or _STATIC_VIEW or _NullView()
    with view.phase(label):
        yield


# ── plan preview ───────────────────────────────────────────────────────────────

# (kind, label, detail): kind "cmd" shows `$ detail`, "script" shows detail as a
# syntax block, "note" is a dim annotation line.
PlanItem = tuple[str, str, str]


def plan_panel(title: str, items: list[PlanItem], note: str = "") -> None:
    """Render the 'here is what will run' preview before a state-changing op."""
    plain = _plain()
    parts: list[RenderableType] = [Text(f"{title} plan", style=f"{PRIMARY} bold")]
    for kind, label, detail in items:
        if kind == "script":
            parts.append(Text(f"  {label}:", style=f"bold {ACCENT}"))
            parts.append(_syntax(detail, "bash"))
        elif kind == "note":
            parts.append(Text(f"  {'-' if plain else '↳'} {detail}", style=SECONDARY))
        else:
            parts.append(
                Text.assemble(("  $ ", f"{ACCENT} bold"), (detail, PRIMARY), overflow="fold")
            )
    if note:
        parts.append(Text(""))
        parts.append(Text(f"  {note}", style=f"{AMBER}"))
    console.print(
        Panel(Group(*parts), box=box.ASCII if plain else box.ROUNDED,
              border_style=BORDER, padding=(0, 1), expand=True)
    )


def batch_plan_panel(rows: list[tuple[str, str]], action: str) -> None:
    """Show exact commands, stacked at widths where two columns crowd them."""
    title = Text(f"{action} plan", style=f"{PRIMARY} bold")
    if console.width < 72:
        console.print(title)
        for name, detail in rows:
            console.print(Text(name, style=f"{ACCENT} bold", overflow="fold"))
            console.print(Text(f"  {detail}", overflow="fold"))
        console.print()
        return
    tbl = table(title=title)
    tbl.add_column("Package", style="bold", ratio=1, overflow="fold")
    tbl.add_column("Will run", ratio=3, overflow="fold")
    for name, detail in rows:
        # Text, not a markup string: a plan is the exact command, and `[lock]` in
        # `ansible-lint[lock]` is text to show, not a style tag for Rich to delete.
        tbl.add_row(Text(name), Text(detail))
    console.print(tbl)
    console.print()


# ── run summary ────────────────────────────────────────────────────────────────

_SUMMARY_STATUS = {
    "ok": (SUCCESS, "Done"),
    "skip": (SECONDARY, "Skipped"),
    "fail": (DESTRUCTIVE, "Failed"),
    "cancelled": (CAUTION, "Cancelled"),
}


def summary(results: list[tuple[str, str, str]], title: str = "Summary") -> None:
    """End-of-run report. `results` rows are (label, status, detail) where
    status is "ok" | "skip" | "fail" | "cancelled" and detail describes the outcome."""
    counts = " | ".join(
        f"{sum(s == status for _, s, _ in results)} {label.lower()}"
        for status, (_, label) in _SUMMARY_STATUS.items()
        if status != "cancelled" or any(s == status for _, s, _ in results)
    )
    heading_text = Text.assemble((title, f"{PRIMARY} bold"), (f" | {counts}", SECONDARY))
    console.print()
    compact = console.width < 72
    if compact:
        console.print(heading_text)
    else:
        tbl = table(title=heading_text)
        tbl.add_column("Status", style="bold", min_width=9)
        tbl.add_column("Package", style="bold", ratio=1, overflow="fold")
        tbl.add_column("Detail", ratio=2, overflow="fold")
    for label, status, detail in results:
        color, word = _SUMMARY_STATUS.get(status, _SUMMARY_STATUS["skip"])
        # `detail` is an installer's error line: arbitrary text, so never parsed as markup
        # (`[/usr/bin/x]` is a closing tag to Rich and raises MarkupError).
        if compact:
            console.print(Text.assemble((f"{word}: ", f"{color} bold"), label, overflow="fold"))
            if detail:
                console.print(Text(f"  {detail}", overflow="fold"))
        else:
            tbl.add_row(Text(word, style=color), Text(label), Text(detail))
    if not compact:
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
    entries = list(choices) + [questionary.Choice(title="[Back]", value=BACK)]
    result = select(prompt, entries, default=default)
    return result


def text_input_back(prompt: str, default: str = "", required: bool = False) -> object:
    """text_input() with Ctrl+B (or :back in plain mode) to return BACK."""
    while True:
        if _static_prompt():
            result = _plain_input(f"{prompt} (type :back to go back)", default)
            if result == ":back":
                return BACK
        else:
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
                [questionary.Choice("[Confirm]", value="__done__")]
                + [
                    questionary.Choice(f"edit {s.label}", value=s.key)
                    for s in applicable
                ]
                + [questionary.Choice("[Cancel]", value="__abort__")],
            )
            if pick in ("__abort__", ""):
                return None
            if pick == "__done__":
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
