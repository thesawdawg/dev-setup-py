from __future__ import annotations

import shutil

from dev_setup import ui
from dev_setup.agent import config as agent_config
from dev_setup.agent.config import DEFAULT_HOST, AgentConfig
from dev_setup.agent.ollama import OllamaClient, OllamaError
from dev_setup.agent.preflight import _is_local, normalize_model

_MANUAL = "Enter a model name manually"


def should_run(*, interactive: bool) -> bool:
    """First run, and only when there is a terminal to drive the prompts. A
    non-interactive invocation (`--print`, a pipe) falls back to defaults instead of
    blocking on questions nobody can answer."""
    return interactive and not agent_config.exists()


def _tool_capable_models(client: OllamaClient) -> list[str]:
    """Locally pulled models that advertise the `tools` capability. Best-effort: a
    model that fails inspection is skipped rather than aborting the wizard."""
    try:
        models = client.list_models()
    except OllamaError:
        return []
    capable = []
    for name in sorted(models):
        try:
            if "tools" in client.capabilities(name):
                capable.append(name)
        except OllamaError:
            continue
    return capable


def _choose_host(default: str) -> str:
    ui.console.print()
    ui.info("Where is Ollama running?")
    host = ui.text_input("Ollama host:", default=default, required=True).rstrip("/")

    if _is_local(host) and shutil.which("ollama") is None:
        ui.warn("Ollama does not appear to be installed on this machine.")
        ui.dim("  Install it with:  devstuff install ollama")
        ui.dim("  Or point the wizard at a remote daemon and re-run.")
    return host


def _choose_model(host: str, default: str) -> str:
    client = OllamaClient(host, timeout=15)
    with ui.spinner(f"looking for tool-capable models at {host}…"):
        capable = _tool_capable_models(client)

    if not capable:
        ui.console.print()
        ui.warn(f"No tool-capable models found at {host}.")
        ui.dim("  The agent needs a model reporting the `tools` capability, e.g.:")
        ui.dim("    ollama pull gemma4")
        ui.dim("  (check any model with `ollama show <name>`)")
        return ui.text_input("Model to use (pull it before running):", default=default, required=True)

    ui.console.print()
    ui.success(f"Found {len(capable)} tool-capable model(s) at {host}.")
    # Preselect the current/default model when it is one of the choices.
    choices = [*capable, _MANUAL]
    choice = ui.select("Which model should the agent use?", choices)
    if choice == _MANUAL:
        return ui.text_input("Model name:", default=default, required=True)
    return choice


def run(existing: AgentConfig | None = None) -> AgentConfig:
    """Walk the user through creating agent.yaml and return the resulting config.

    Deliberately configures only host, model and reasoning visibility -- the three
    things a new user must decide. Everything else keeps its default and can be
    hand-edited later; a wizard that asks about num_ctx and timeouts on first run
    would be worse than one that asks nothing.

    `existing` (from a re-run via --setup) seeds the prompts with current values.
    """
    base = existing or AgentConfig()
    ui.section("Set up devstuff agent")
    if existing:
        ui.dim("Reconfiguring — current values are pre-filled. Ctrl-C to keep them.")
    else:
        ui.dim("No configuration found — let's create one. Ctrl-C to skip.")

    host = _choose_host(base.host if existing else DEFAULT_HOST)
    model = _choose_model(host, base.model)

    think = ui.confirm("Show the model's reasoning while it works?", default=base.think)

    config = AgentConfig(model=normalize_model(model), host=host, think=think)
    path = agent_config.save(config)

    ui.console.print()
    ui.success(f"Saved {path}")
    ui.dim(f"  model  {config.model}")
    ui.dim(f"  host   {config.host}")
    ui.dim("  Edit that file any time, or re-run with --model / --host to override.")
    ui.console.print()
    return config
