"""`devstuff tui` — the devstuff workspace in the terminal.

Plain `devstuff tui` opens the model-free workspace: menus drive the same
scoped actions the assistant uses — find tools, inspect definitions, guided
catalog drafts — with the same previews and approvals. No model is started.

`--chat` opts into the local Ollama-backed scoped assistant (the same
backend as `devstuff agent`), restricted to loopback hosts. `/browse` inside
the chat opens the read-only catalog browser and returns to the same
conversation; `--browse` goes straight to that browser.

Heavy imports (registry, the renderer, the agent) stay inside the functions so
a plain `devstuff` startup doesn't pay for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from dev_setup.base import Tool

if TYPE_CHECKING:
    from dev_setup.tui import Entry


def _one_line(value: str) -> str:
    """Collapse catalog text to a safe single line — control bytes, embedded
    newlines and ANSI/format characters become ordinary spacing."""
    return " ".join(
        "".join(char if char.isprintable() else " " for char in value).split()
    )


def _plan_label(kind: str, label: str, detail: str) -> str:
    """One safe display line per plan item. `cmd` details are argv for a
    preview, never execution; `script` bodies are dropped entirely — raw
    shell text is not terminal-safe and not the point of a plan review."""
    if kind == "cmd":
        return _one_line(f"{label}: {detail}" if label else detail)
    if kind == "script":
        return _one_line(f"{label or 'install script'}: script body omitted")
    return _one_line(detail)


def _entry(tool: Tool) -> Entry:
    from dev_setup import tui
    from dev_setup.commands._plan import plan_for

    return tui.Entry(
        key=_one_line(tool.key),
        description=_one_line(getattr(tool, "description", "") or ""),
        category=_one_line(getattr(tool, "category", "") or "custom"),
        source=_one_line(getattr(tool, "install_type", "") or "unknown"),
        plan=tuple(_plan_label(*item) for item in plan_for(tool, "install")),
    )


def browse_catalog() -> None:
    """The read-only catalog browser — `tui --browse` and `/browse` in chat."""
    from dev_setup import registry, tui
    from dev_setup.catalog import CatalogError

    try:
        tools = registry.all_tools()
    except CatalogError as exc:
        raise click.ClickException(str(exc)) from exc
    tui.browse(
        [_entry(t) for t in tools],
        title="devstuff tools (read-only catalog preview)",
    )


@click.command("tui")
@click.option(
    "--browse",
    is_flag=True,
    help="Open the read-only catalog browser instead of the workspace.",
)
@click.option(
    "--chat",
    is_flag=True,
    help="Start the assistant chat (Ollama local, or an OpenAI endpoint).",
)
@click.option(
    "--provider",
    type=click.Choice(["ollama", "openai"]),
    default=None,
    help="Chat provider (with --chat).",
)
@click.option(
    "--model", default=None,
    help="Model to use (with --chat; Ollama tag or provider model ID).",
)
@click.option(
    "--host", default=None,
    help="Endpoint URL (with --chat; Ollama loopback or provider base URL).",
)
@click.option(
    "--api-key-env", default=None,
    help="Env var holding the API key (with --chat --provider openai).",
)
@click.option(
    "--profile", "profile_ref", metavar="PROFILE", default=None,
    help="Open this profile (@bundle or file) in the workspace on launch.",
)
def tui_cmd(
    browse: bool,
    chat: bool,
    model: str | None,
    host: str | None,
    provider: str | None,
    api_key_env: str | None,
    profile_ref: str | None,
) -> None:
    """Find and draft devstuff tools; --chat adds the assistant."""
    if browse and chat:
        raise click.UsageError("--browse and --chat are separate modes.")
    if profile_ref and (browse or chat):
        raise click.UsageError("--profile doesn't combine with --browse or --chat.")
    if (model or host or provider or api_key_env) and not chat:
        raise click.UsageError(
            "--model/--host/--provider/--api-key-env apply only to --chat."
        )
    if api_key_env and provider != "openai":
        raise click.UsageError("--api-key-env applies only to --provider openai.")

    from dev_setup.commands.configure_cmd import require_prompt

    require_prompt(
        "The TUI needs a terminal. Use devstuff list for non-interactive inventory."
    )
    if browse:
        browse_catalog()
        return

    def start_chat() -> None:
        from dev_setup.agent.chat_setup import start_chat as _start_chat

        _start_chat(
            model=model,
            host=host,
            provider=provider,
            api_key_env=api_key_env,
            browse_catalog=browse_catalog,
        )

    if chat:
        start_chat()
        return

    from dev_setup.tui_workspace import run_workspace

    run_workspace(start_chat, profile_ref=profile_ref)
