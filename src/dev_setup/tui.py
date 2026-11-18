"""Experimental read-only catalog browser backing `devstuff tui`.

Deliberately separate from the production install paths: entries are plain
data (no Tool objects, no registry/generic/commands imports), nothing here
probes installed state, resolves requirements, or executes anything. The one
public seam is `browse()` — the command layer feeds catalog entries and the
dev fixture (dev/tui_preview.py) feeds static ones, so a single renderer is
kept honest instead of two copied UIs.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from textwrap import fill

from dev_setup.theme import ACCENT, PRIMARY, SECONDARY

MIN_COLUMNS = 80
MIN_ROWS = 16

_READ_ONLY_NOTE = "Read-only. Installed state and requirements not checked."


@dataclass(frozen=True)
class Entry:
    key: str
    description: str
    category: str
    source: str
    plan: tuple[str, ...]


def _one_line(value: str) -> str:
    """Safe single-line text for the terminal — control characters, embedded
    newlines and ANSI/format bytes from catalog text become plain spacing."""
    return " ".join(
        "".join(char if char.isprintable() else " " for char in value).split()
    )


def _terminal_columns() -> int:
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except OSError:
        return 0


def _too_small() -> bool:
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except OSError:
        return False
    return size.columns < MIN_COLUMNS or size.lines < MIN_ROWS


def _plain_inventory(entries: Sequence[Entry], title: str) -> None:
    """Static dump for narrow/dumb/plain terminals and post-shrink fallback —
    no ANSI, no repaints, marks already made exist only in memory."""
    columns = _terminal_columns() or 78
    print(fill(title, width=columns))
    print(fill(_READ_ONLY_NOTE, width=columns))
    print()
    for e in entries:
        print(fill(
            f"- {_one_line(e.key)}: {_one_line(e.description)} "
            f"({_one_line(e.category)} · {_one_line(e.source)})",
            width=columns,
        ))


def _interactive(entries: Sequence[Entry], title: str) -> bool:
    """Inline (non-alternate-screen) browser. Returns True if the terminal
    shrank below the minimum mid-run, so the caller can print the plain
    inventory instead."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.output import ColorDepth
    from prompt_toolkit.styles import Style

    # Foreground colors only — never paint a background; marks and the cursor
    # are ASCII > and [x] so nothing depends on hue. NO_COLOR forces 1-bit.
    style = Style.from_dict({
        "heading": f"bold {PRIMARY}",
        "meta": SECONDARY,
        "cursor": f"bold {ACCENT}",
    })
    cursor = 0
    marked: set[str] = set()
    show_detail = False

    def reset_cursor(_buffer):
        nonlocal cursor
        cursor = 0

    query = Buffer(multiline=False, on_text_changed=reset_cursor)

    def matches() -> list[Entry]:
        terms = query.text.casefold().split()
        return [
            e for e in entries
            if all(
                term in f"{e.key} {e.description} {e.category} {e.source}".casefold()
                for term in terms
            )
        ]

    def inventory():
        visible = matches()
        # Windowed rows: the pane is 10 lines — this heading, at most 8
        # entries, and the range footer — so anything beyond scrolls with
        # the cursor instead of clipping outside the viewport.
        offset = max(0, cursor - 7)
        rows = [("class:heading", f"Tools ({len(visible)}/{len(entries)})\n")]
        for i, e in enumerate(visible[offset:offset + 8], offset):
            pointed = i == cursor
            check = "[x]" if e.key in marked else "[ ]"
            rows.append((
                "class:cursor" if pointed else "",
                f"{'>' if pointed else ' '} {check} {_one_line(e.key):<16.16} "
                f"{_one_line(e.category):.7}\n",
            ))
        if not visible:
            rows.append(("", "No matches"))
        elif len(visible) > 8:
            rows.append((
                "class:meta",
                f"  {offset + 1}-{min(offset + 8, len(visible))} of {len(visible)}",
            ))
        return rows

    def panel():
        if show_detail:
            visible = matches()
            heading = "Nominal install plan (dependencies not resolved)"
            if not visible:
                lines = ("No matching tool.",)
            else:
                e = visible[cursor]
                lines = (
                    f"{_one_line(e.key)} via {_one_line(e.source)}",
                    *(f"{i}. {_one_line(step)}"
                      for i, step in enumerate(e.plan, 1)),
                    "",
                    _one_line(e.description),
                )
        else:
            picked = [e for e in entries if e.key in marked]
            heading = f"Marked for review ({len(picked)}; marks survive filters)"
            if not picked:
                lines = ("Space marks tools for review. Nothing runs.",)
            else:
                shown = picked[:8]
                lines = tuple(
                    f"- {_one_line(e.key)} ({_one_line(e.category)} · "
                    f"{_one_line(e.source)})" for e in shown
                )
                if len(picked) > len(shown):
                    lines += (
                        f"+{len(picked) - len(shown)} more marked; "
                        "filter to inspect individually",
                    )
        return [("class:heading", heading + "\n"), ("", "\n".join(lines))]

    list_control = FormattedTextControl(inventory, focusable=True, show_cursor=False)
    filter_control = BufferControl(buffer=query)
    layout = Layout(
        HSplit([
            Window(FormattedTextControl(title), height=1, style="class:heading"),
            Window(FormattedTextControl(_READ_ONLY_NOTE), height=1,
                   style="class:meta"),
            VSplit([
                Window(FormattedTextControl("Filter: "), width=8, height=1),
                Window(filter_control, height=1),
            ]),
            VSplit([
                Window(list_control, width=30),
                Window(width=2),
                Window(FormattedTextControl(panel), wrap_lines=True),
            ], height=10),
            Window(FormattedTextControl(lambda: (
                "Up/Down move | Space mark | Enter detail/review\n"
                "/ or Tab filter | q or Ctrl+C quit"
                if layout.current_control is list_control
                else "Type to filter | Enter/Esc/Tab back to tools\n"
                     "Ctrl+C quits"
            )),
                height=2, style="class:meta"),
        ]),
        focused_element=list_control,
    )
    browsing = Condition(lambda: layout.current_control is list_control)
    keys = KeyBindings()

    @keys.add("/", filter=browsing)
    @keys.add("tab", filter=browsing)
    def edit_filter(event):
        event.app.layout.focus(filter_control)

    @keys.add("enter", filter=~browsing)
    @keys.add("escape", filter=~browsing)
    @keys.add("tab", filter=~browsing)
    def leave_filter(event):
        event.app.layout.focus(list_control)

    @keys.add("up", filter=browsing)
    @keys.add("down", filter=browsing)
    def move(event):
        nonlocal cursor
        offset = -1 if event.key_sequence[-1].key == "up" else 1
        cursor = max(0, min(cursor + offset, len(matches()) - 1))

    @keys.add("space", filter=browsing)
    def toggle_mark(event):
        visible = matches()
        if visible:
            marked.symmetric_difference_update((visible[cursor].key,))

    @keys.add("enter", filter=browsing)
    def toggle_detail(event):
        nonlocal show_detail
        show_detail = not show_detail

    @keys.add("q", filter=browsing)
    @keys.add("c-c")
    def quit_browser(event):
        event.app.exit(result=False)

    def check_size(app):
        if not app.is_done and _too_small():
            app.exit(result=True)

    app = Application(
        layout=layout,
        key_bindings=keys,
        style=style,
        full_screen=False,
        erase_when_done=True,
        before_render=check_size,
        color_depth=ColorDepth.DEPTH_1_BIT if "NO_COLOR" in os.environ else None,
    )
    try:
        return bool(app.run())
    except (EOFError, KeyboardInterrupt):
        return False


def browse(entries: Sequence[Entry], *, title: str) -> None:
    """Browse `entries`: inline prompt_toolkit view on a real terminal of at
    least 80x16, otherwise a plain static inventory. Read-only by contract —
    entries are data, nothing here executes."""
    entries = tuple(entries)
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    term = os.environ.get("TERM", "dumb")
    if (
        not tty
        or term in {"", "dumb"}
        or os.environ.get("DEVSTUFF_PLAIN") == "1"
        or _too_small()
        or _interactive(entries, title)
    ):
        _plain_inventory(entries, title)
