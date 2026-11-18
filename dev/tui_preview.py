"""Package F fixture spike: run `.venv/bin/python -B dev/tui_preview.py`."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from textwrap import fill

MIN_COLUMNS = 80
MIN_ROWS = 16


@dataclass(frozen=True)
class Tool:
    name: str
    source: str
    status: str
    action: str
    steps: tuple[str, ...]
    log: str


TOOLS = (
    Tool("docker", "apt", "missing", "Install",
         ("Add package source (sudo)", "Install engine and plugins (sudo)",
          "Verify CLI and Compose plugin"),
         "[failed] Engine present; Compose not found."),
    Tool("lazygit", "archive", "update ready", "Update",
         ("Fetch release archive", "Replace binary", "Verify version"),
         "[done] Version check passed."),
    Tool("ripgrep", "apt", "current", "Skip",
         ("No version change planned",), "[skipped] Already current."),
    Tool("shellcheck", "apt", "missing", "Install",
         ("Install package (sudo)", "Verify binary"), "[queued] Waiting for apt."),
    Tool("uv", "script", "update ready", "Update",
         ("Fetch installer", "Run installer", "Verify version"),
         "[silent] No output from installer for 31s."),
    Tool("commitizen", "uv tool", "missing", "Install",
         ("Resolve package via uv", "Expose CLI", "Verify binary"),
         "[queued] Waiting for uv update."),
    Tool("fzf", "apt", "current", "Skip",
         ("No version change planned",), "[skipped] Already current."),
)


def detail_lines(tool: Tool) -> tuple[str, ...]:
    return (
        f"{tool.name}: {tool.action} via {tool.source}",
        *(f"{i}. {step}" for i, step in enumerate(tool.steps, 1)),
        "",
        "Example log (static):",
        tool.log,
    )


def plain_preview(reason: str, columns: int = 78) -> None:
    lines = (
        "devstuff TUI preview - fixtures only",
        f"Plain preview: {reason}. No keyboard controls.",
        "",
        "Tools (state / source -> sample plan action):",
        *(f"- {t.name}: {t.status} / {t.source} -> {t.action}" for t in TOOLS),
        "",
        "Sample detail:",
        *detail_lines(TOOLS[0]),
    )
    for line in lines:
        print(fill(line, width=columns))


def terminal_size() -> os.terminal_size:
    try:
        return os.get_terminal_size(sys.stdout.fileno())
    except OSError:
        return os.terminal_size((0, 0))


def too_small(size: os.terminal_size) -> bool:
    return size.columns < MIN_COLUMNS or size.lines < MIN_ROWS


def interactive_preview() -> bool:
    """Return True if the terminal became too small during the preview."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.styles import Style

    # Local copies of the spec's U1 roles, not an import of runtime UI state.
    # No backgrounds; text, cursor and mark indicators work without color.
    style = Style.from_dict({
        "heading": "bold #e5e7eb",
        "meta": "#a8b3c2",
        "cursor": "bold #67e8f9",
    })
    cursor = 0
    marked: set[str] = set()
    show_detail = False

    def reset_cursor(_buffer):
        nonlocal cursor
        cursor = 0

    query = Buffer(multiline=False, on_text_changed=reset_cursor)

    def matches() -> list[Tool]:
        terms = query.text.casefold().split()
        return [
            t for t in TOOLS
            if all(term in f"{t.name} {t.status} {t.source}" for term in terms)
        ]

    def inventory():
        visible = matches()
        rows = [("class:heading", f"Tools ({len(visible)}/{len(TOOLS)})\n")]
        for i, tool in enumerate(visible):
            pointed = i == cursor
            check = "[x]" if tool.name in marked else "[ ]"
            rows.append((
                "class:cursor" if pointed else "",
                f"{'>' if pointed else ' '} {check} {tool.name:<11} {tool.status}\n",
            ))
        if not visible:
            rows.append(("", "No matches"))
        return rows

    def panel():
        if show_detail:
            visible = matches()
            title = "Tool detail / fixture log"
            lines = detail_lines(visible[cursor]) if visible else ("No matching tool.",)
        else:
            title = f"Fixture plan ({len(marked)} marked; includes hidden picks)"
            lines = tuple(
                f"- {t.action:<7} {t.name} ({t.source})" for t in TOOLS if t.name in marked
            ) or ("Space marks tools for review. Nothing runs.",)
        return [("class:heading", title + "\n"), ("", "\n".join(lines))]

    list_control = FormattedTextControl(inventory, focusable=True, show_cursor=False)
    filter_control = BufferControl(buffer=query)
    browsing = Condition(lambda: layout.current_control is list_control)
    layout = Layout(
        HSplit([
            Window(FormattedTextControl("devstuff TUI preview - fixtures only"), height=1,
                   style="class:heading"),
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
                "Up/Down move | Space mark | Enter detail/plan | / filter | q quit"
                if browsing() else "Type name, state or source | Enter/Esc back to tools"
            ) + "\nTab switches focus | Ctrl+C quits"),
                height=2, style="class:meta"),
        ]),
        focused_element=list_control,
    )
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
            marked.symmetric_difference_update((visible[cursor].name,))

    @keys.add("enter", filter=browsing)
    def toggle_detail(event):
        nonlocal show_detail
        show_detail = not show_detail

    @keys.add("q", filter=browsing)
    @keys.add("c-c")
    def quit_preview(event):
        event.app.exit(result=False)

    def check_size(app):
        if not app.is_done and too_small(terminal_size()):
            app.exit(result=True)

    # Inline (not alternate-screen), and no persistent history or external editor.
    app = Application(
        layout=layout, key_bindings=keys, style=style, full_screen=False,
        erase_when_done=True, before_render=check_size,
    )
    try:
        return app.run()
    except (EOFError, KeyboardInterrupt):
        return False


def main() -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        plain_preview("stdin and stdout must both be terminals")
        return

    size = terminal_size()
    columns = size.columns or 78
    if os.environ.get("TERM", "dumb") in {"", "dumb"}:
        plain_preview("terminal does not advertise interactive support", columns)
    elif too_small(size) or interactive_preview():
        plain_preview(f"interactive view needs {MIN_COLUMNS} columns x {MIN_ROWS} rows",
                      terminal_size().columns or 78)


if __name__ == "__main__":
    main()
