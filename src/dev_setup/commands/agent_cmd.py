from __future__ import annotations

import sys
from pathlib import Path

import click

from dev_setup import ui
from dev_setup.agent import config as agent_config
from dev_setup.agent import preflight, registry, sandbox, session
from dev_setup.agent.approval import ApprovalPolicy
from dev_setup.agent.ollama import OllamaClient
from dev_setup.agent.sandbox import SandboxError, Workspace
from dev_setup.catalog import CatalogError


def _select_workspace(dir_opt: str | None, *, interactive: bool) -> Workspace:
    """Choose the directory the agent will be confined to, and warn before
    handing over anything sensitive."""
    if dir_opt is None and interactive:
        dir_opt = ui.text_input("Workspace directory:", default=str(Path.cwd()), required=True)

    try:
        workspace = Workspace.create(dir_opt or Path.cwd())
    except SandboxError as exc:
        ui.error(str(exc))
        sys.exit(1)

    warnings = sandbox.assess(workspace.root)
    if warnings:
        ui.console.print()
        for warning in warnings:
            ui.warn(warning)
        ui.console.print()
        # Without a terminal there is nobody to answer, so warn and continue --
        # the caller passed --dir explicitly and the sandbox still applies.
        if interactive and not ui.confirm("Use this directory anyway?", default=False):
            ui.dim("Aborted.")
            sys.exit(1)

    return workspace


@click.command("agent")
@click.option("--dir", "workspace_dir", default=None, help="Workspace directory (skips the prompt).")
@click.option("--model", default=None, help="Ollama model to use (overrides agent.yaml).")
@click.option("--host", default=None, help="Ollama host URL (overrides agent.yaml).")
@click.option("--print", "one_shot", default=None, help="Run a single prompt and exit.")
@click.option(
    "--yolo",
    is_flag=True,
    help="Run mutating tools without confirmation (the denylist still applies).",
)
def agent_cmd(
    workspace_dir: str | None,
    model: str | None,
    host: str | None,
    one_shot: str | None,
    yolo: bool,
) -> None:
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

    interactive = sys.stdin.isatty()
    if one_shot is None and not interactive:
        ui.error("devstuff agent needs a terminal.")
        ui.dim('Use --print "your prompt" for non-interactive use.')
        sys.exit(1)

    workspace = _select_workspace(workspace_dir, interactive=interactive and one_shot is None)

    try:
        tools = registry.build()
    except CatalogError as exc:
        ui.error(str(exc))
        sys.exit(1)

    policy = ApprovalPolicy(
        yolo=yolo,
        auto_approve=cfg.auto_approve,
        can_prompt=interactive and one_shot is None,
    )
    sess = session.AgentSession(
        client, cfg, workspace, model=resolved, tools=tools, policy=policy
    )

    if one_shot is not None:
        session.run_once(sess, one_shot)
        return

    session.run_repl(sess)
