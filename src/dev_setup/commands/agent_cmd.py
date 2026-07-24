from __future__ import annotations

import sys

import click

from dev_setup import ui
from dev_setup.agent import config as agent_config
from dev_setup.agent import preflight, session
from dev_setup.agent.ollama import OllamaClient
from dev_setup.catalog import CatalogError


@click.command("agent")
@click.option("--model", default=None, help="Ollama model to use (overrides agent.yaml).")
@click.option("--host", default=None, help="Ollama host URL (overrides agent.yaml).")
@click.option("--print", "one_shot", default=None, help="Run a single prompt and exit.")
def agent_cmd(model: str | None, host: str | None, one_shot: str | None) -> None:
    """Chat with a local model. See: devstuff agent --help"""
    try:
        cfg = agent_config.load()
    except CatalogError as exc:
        ui.error(str(exc))
        ui.dim(f"Config: {agent_config.USER_CONFIG_PATH}")
        sys.exit(1)

    agent_config.apply_overrides(cfg, model=model, host=host)
    client = OllamaClient(cfg.host, timeout=cfg.request_timeout)

    try:
        with ui.spinner("checking ollama…"):
            resolved = preflight.check(cfg, client)
    except preflight.PreflightError as exc:
        ui.error(str(exc))
        for remedy in exc.remedies:
            ui.dim(f"  {remedy}")
        sys.exit(1)

    sess = session.AgentSession(client, cfg, model=resolved)

    if one_shot is not None:
        session.run_once(sess, one_shot)
        return

    if not sys.stdin.isatty():
        ui.error("devstuff agent needs a terminal.")
        ui.dim('Use --print "your prompt" for non-interactive use.')
        sys.exit(1)

    session.run_repl(sess)
