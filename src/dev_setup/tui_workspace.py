"""`devstuff tui` — the model-free workspace.

Menus drive the same `ScopedActions` the assistant model uses, so searching,
inspecting, drafting and registering behave (and ask) exactly as they do in
chat — the only difference is that a person picks each action instead of a
model proposing it. Nothing here probes installed state, and the model is
touched only by 'Enable local chat'.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import click
import questionary
from rich.panel import Panel
from rich.text import Text

from dev_setup import catalog, ui
from dev_setup.agent.loop import ToolOutcome
from dev_setup.agent.ollama import ToolCall
from dev_setup.agent.scoped import ScopedActions

# The same bound the agent loop applies by default: a runaway function's
# output should still not flood the terminal, but the workspace is not bound
# by what a model context can hold elsewhere (e.g. definition() is uncapped).
_TOOL_OUTPUT_BYTES = 8000

_CANCELLED = object()

_MENU = [
    questionary.Choice("Find tools", value="find"),
    questionary.Choice("Draft catalog entry", value="draft"),
    questionary.Choice("Enable chat", value="chat"),
    questionary.Choice("Quit", value="quit"),
]

_BACK = questionary.Choice(title="Back", value=ui.BACK)


def _one_line(value: str) -> str:
    """Collapse catalog text to a safe single line for a picker row."""
    return " ".join(
        "".join(char if char.isprintable() else " " for char in value).split()
    )


def _prompt(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """One workspace prompt. Ctrl+C/EOF *on a menu or field* means 'back to
    the previous step' — the same keys landing inside a real action (an
    install, a function run) still propagate as an interrupt."""
    try:
        return fn(*args, **kwargs)
    except (KeyboardInterrupt, EOFError):
        return _CANCELLED


def _show(outcome: ToolOutcome) -> None:
    """Tool output is untrusted catalog/script text — render it literally."""
    ui.console.print()
    ui.console.print(Text(outcome.content))
    ui.console.print()


def _find_tools(actions: ScopedActions) -> None:
    query = _prompt(ui.text_input, "Search packages and functions (blank lists all)")
    if query is _CANCELLED:
        return
    try:
        rows = actions.find(query)
    except RuntimeError as exc:
        ui.console.print(Text(str(exc)))
        return
    if not rows:
        ui.console.print(
            Text(
                f"Nothing matches {query!r}."
                if query
                else "The catalog is empty."
            )
        )
        return
    picked = _prompt(
        ui.select,
        "Tools",
        [
            questionary.Choice(
                title=_one_line(
                    f"{r['kind']:<8} {r['key']:<18} {r['description']}"
                ),
                value=(r["kind"], r["key"]),
            )
            for r in rows
        ]
        + [_BACK],
        searchable=True,
    )
    if picked is _CANCELLED or picked is ui.BACK or not picked:
        return
    kind, key = picked
    _inspect(actions, kind, key)


def _inspect(actions: ScopedActions, kind: str, key: str) -> None:
    try:
        data = actions.definition(kind, key)
    except RuntimeError as exc:
        ui.console.print(Text(str(exc)))
        return
    body: dict[str, Any] = {"kind": kind, "key": key, "definition": data}
    if data.get("docs_url"):
        body["docs_url"] = data["docs_url"]
    ui.console.print()
    ui.console.print(
        Panel(
            Text(json.dumps(body, indent=2, ensure_ascii=True, default=str)),
            title=f"{kind} '{key}'",
            border_style=ui.BORDER,
        )
    )

    options = [_BACK]
    if kind == "package":
        options.insert(0, questionary.Choice("Review install…", value="install"))
    elif str(data.get("type", "")) == "script":
        options.insert(0, questionary.Choice("Review run…", value="run"))
    else:
        ui.dim(f"type {data.get('type')!r} functions are inspect-only here.")
    picked = _prompt(ui.select, "Action", options)
    if picked is _CANCELLED or picked is ui.BACK or not picked:
        return

    if picked == "install":
        _show(
            actions.execute(
                ToolCall(name="install_tool", arguments={"key": key}),
                _TOOL_OUTPUT_BYTES,
            )
        )
    elif picked == "run":
        named = _gather_params(key, data)
        if named is None:
            return
        _show(
            actions.execute(
                ToolCall(
                    name="run_function",
                    arguments={"key": key, "arguments_json": json.dumps(named)},
                ),
                _TOOL_OUTPUT_BYTES,
            )
        )


def _gather_params(key: str, data: dict[str, Any]) -> dict[str, str] | None:
    """Named arguments for a script function — the function's own declared
    params drive the prompts; None means the user cancelled."""
    from dev_setup.functions_registry import FunctionDef

    fn = FunctionDef.from_dict(copy.deepcopy(data), key)
    named: dict[str, str] = {}
    for param in fn.params:
        label = param.name + (f" — {param.description}" if param.description else "")
        value = _prompt(
            ui.text_input, f"{label}:", default=param.default, required=param.required
        )
        if value is _CANCELLED:
            return None
        named[param.name] = value
    return named


def _draft(actions: ScopedActions) -> None:
    kind = _prompt(
        ui.select,
        "New catalog entry",
        [
            questionary.Choice("APT package", value="apt"),
            questionary.Choice("uv tool", value="uvx"),
            questionary.Choice("Script function", value="function"),
            _BACK,
        ],
    )
    if kind is _CANCELLED or kind is ui.BACK or not kind:
        return
    key = _prompt(ui.text_input, "Catalog key (e.g. my-tool)", required=True)
    if key is _CANCELLED:
        return
    if not catalog.VALID_KEY.fullmatch(key):
        ui.console.print(
            Text(f"invalid key {key!r}: must match {catalog.VALID_KEY.pattern}")
        )
        return
    description = _prompt(ui.text_input, "Description", required=True)
    if description is _CANCELLED:
        return

    if kind == "function":
        ui.dim("One-line shell script — you write it; it is not generated,")
        ui.dim("and it is not run while drafting or saving.")
        script = _prompt(ui.text_input, "Script", required=True)
        if script is _CANCELLED:
            return
        definition: dict[str, Any] = {
            "type": "script",
            "description": description,
            "script": script,
        }
        draft_kind = "function"
    else:
        field = "apt_packages" if kind == "apt" else "pip_name"
        label = "apt package names" if kind == "apt" else "PyPI package name"
        value = _prompt(ui.text_input, f"{label} (required)", required=True)
        if value is _CANCELLED:
            return
        definition = {"type": kind, "description": description, field: value}
        draft_kind = "package"

    docs = _prompt(ui.text_input, "Documentation URL (optional)")
    if docs is _CANCELLED:
        return
    if docs:
        definition["docs_url"] = docs

    # Generous cap for drafting alone: the input is <=16KiB but normalization
    # plus JSON escaping can expand the echoed payload, and it must stay
    # parseable for the draft_id. The full definition is not printed here —
    # the registration preview immediately shows the same normalized payload.
    outcome = actions.execute(
        ToolCall(
            name="draft_extension",
            arguments={
                "kind": draft_kind,
                "key": key,
                "definition_json": json.dumps(definition),
            },
        ),
        128 * 1024,
    )
    if not outcome.ok:
        _show(outcome)
        return
    try:
        draft_id = json.loads(outcome.content)["draft_id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        ui.error("draft did not return an id — nothing to register.")
        return

    # Registration shows the full normalized entry and asks default-No inside
    # the scoped backend — exactly as when the model proposes it.
    outcome = actions.execute(
        ToolCall(name="register_extension", arguments={"draft_id": draft_id}),
        _TOOL_OUTPUT_BYTES,
    )
    _show(outcome)
    if not outcome.ok and actions.discard_draft(draft_id):
        ui.dim("Draft discarded — nothing was saved.")


def run_workspace(
    start_chat: Callable[[], None], *, profile_ref: str | None = None
) -> None:
    """The `devstuff tui` default: a model-free catalog workspace.
    `start_chat` is invoked only when the user picks 'Enable chat'.
    `profile_ref` (`@bundle` or file) is opened immediately — an explicit
    launch request; a profile stays 'current' in memory only, and reopening
    it re-reads the file so drift on disk shows up."""
    from dev_setup import tui_profile

    ui.dim("devstuff — read-only until you approve an action. Model-free;")
    ui.dim("the assistant is opt-in from the menu below.")
    ui.console.print()
    actions = ScopedActions(can_prompt=True)
    current = profile_ref
    if current is not None and not tui_profile.show_profile(current):
        current = None
    while True:
        menu: list[questionary.Choice] = []
        if current:
            menu.append(
                questionary.Choice(
                    f"Current profile: {_one_line(current)}", value="profile"
                )
            )
        menu += [
            questionary.Choice("Choose a starter profile", value="starter"),
            questionary.Choice("Open a profile file", value="open"),
            *_MENU,
        ]
        try:
            choice = ui.select("devstuff", menu)
        except (KeyboardInterrupt, EOFError):
            ui.console.print()
            return
        if choice in ("", "quit") or choice is ui.BACK:
            return
        if choice == "profile" and current is not None:
            # A failed reopen (file edited into an error, catalog problem)
            # keeps the reference: the user can fix it and try again.
            tui_profile.show_profile(current)
        elif choice == "starter":
            ref = tui_profile.choose_starter()
            if ref is not None and tui_profile.show_profile(ref):
                current = ref
        elif choice == "open":
            ref = tui_profile.open_profile()
            if ref is not None and tui_profile.show_profile(ref):
                current = ref
        elif choice == "find":
            _find_tools(actions)
        elif choice == "draft":
            _draft(actions)
        elif choice == "chat":
            try:
                start_chat()
            except click.ClickException as exc:
                ui.console.print(Text(str(exc)))
            except SystemExit:
                # A declined or failed chat start returns to the menu.
                pass
            ui.console.print()
