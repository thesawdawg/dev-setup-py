from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import click

from dev_setup import ui
from dev_setup.agent import config as agent_config
from dev_setup.agent import preflight, registry, sandbox, session, wizard
from dev_setup.agent.approval import ApprovalPolicy
from dev_setup.agent.ollama import ChatClient, OllamaClient, loopback_host
from dev_setup.agent.sandbox import SandboxError, Workspace
from dev_setup.agent.transcript import Transcript
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


# Candidate model for a first-time local chat — never written to config, and
# preflight still verifies the daemon actually has it (or names what it has).
# Chosen for the 8 GB CPU-only baseline, not measured here.
_LOCAL_CHAT_MODEL = "qwen2.5:3b"
_LOCAL_CHAT_NUM_CTX = 4096


def run_agent(
    *,
    model: str | None = None,
    host: str | None = None,
    workspace_dir: str | None = None,
    workspace_mode: bool = False,
    one_shot: str | None = None,
    yolo: bool = False,
    setup: bool = False,
    browse_catalog: Callable[[], None] | None = None,
    local_chat: bool = False,
) -> None:
    """Shared entry for `devstuff agent` and `devstuff tui`.

    Default is the scoped assistant: the model may search/read the catalog,
    draft extensions, and — each with an explicit approval — install named
    packages or run named script functions. `--workspace`/`--dir` selects the
    legacy workspace agent with its shell/filesystem tools and approval policy.
    """
    legacy = workspace_mode or workspace_dir is not None
    # A piped stdout cannot show a scoped approval: interactive means the user
    # is actually at the terminal, except legacy mode keeps its own tty check
    # (its --print non-approval path is already policy-gated).
    interactive = sys.stdin.isatty() and (legacy or sys.stdout.isatty())
    if one_shot is None and not interactive:
        ui.error("devstuff agent needs a terminal.")
        ui.dim('Use --print "your prompt" for non-interactive use.')
        sys.exit(1)

    if yolo and not legacy:
        ui.error("--yolo only applies to the workspace agent (--workspace or --dir).")
        ui.dim("Scoped actions always ask before running.")
        sys.exit(1)

    # --setup forces the wizard even when a config exists. Otherwise it runs only on
    # first run with a terminal, offering to build a config rather than silently
    # falling back to defaults. Explicit --model/--host means the user is already
    # steering, so don't interrupt them; --print stays scriptable on defaults.
    if setup and not interactive:
        ui.error("--setup needs a terminal.")
        sys.exit(1)
    if setup or (
        # local_chat never runs the auto wizard: a first-time prompt could
        # steer someone at an arbitrary endpoint, which is the opposite of
        # what the loopback-only mode is for.
        not local_chat
        and wizard.should_run(interactive=interactive and one_shot is None)
        and not (model or host)
    ):
        try:
            # A broken existing config is exactly why someone re-runs --setup, so
            # fall back to fresh defaults rather than letting the load error abort it.
            try:
                existing = agent_config.load() if agent_config.exists() else None
            except CatalogError:
                existing = None
            wizard.run(existing)
        except (KeyboardInterrupt, EOFError):
            if not agent_config.exists():
                ui.dim("Skipped setup — using defaults. Run again to configure.")
                ui.console.print()
            else:
                ui.dim("Setup cancelled — keeping the existing configuration.")
                ui.console.print()

    had_config = agent_config.exists()
    try:
        cfg = agent_config.load()
    except CatalogError as exc:
        ui.error(str(exc))
        ui.dim(f"Config: {agent_config.USER_CONFIG_PATH}")
        sys.exit(1)

    agent_config.apply_overrides(cfg, model=model, host=host)
    if local_chat:
        # Validated before the client exists: loopback only, no redirects,
        # no cloud-model names. A remote host stays reachable via the
        # regular `devstuff agent` path, which is explicitly configured.
        cfg.host = loopback_host(cfg.host)
        if not had_config and not model:
            cfg.model = _LOCAL_CHAT_MODEL
        if cfg.model.endswith(":cloud") or cfg.model.endswith("-cloud"):
            raise click.UsageError(
                f"'{cfg.model}' names an Ollama cloud model — local chat "
                f"runs only models on this machine. `ollama pull "
                f"{_LOCAL_CHAT_MODEL}` is a small local option; "
                "`devstuff agent` still supports explicit remote hosts."
            )
        cfg.num_ctx = min(cfg.num_ctx, _LOCAL_CHAT_NUM_CTX)
        ui.dim(
            "local chat talks to a loopback Ollama only. For fully local "
            "operation\nstart the daemon with OLLAMA_NO_CLOUD=1 — the "
            "client cannot verify\nthe server's own setting."
        )
    client = OllamaClient(
        cfg.host, timeout=cfg.request_timeout, local_chat=local_chat
    )

    try:
        with ui.spinner("checking ollama…"):
            resolved = preflight.check(cfg, client)
    except preflight.PreflightError as exc:
        ui.error(str(exc))
        for remedy in exc.remedies:
            ui.dim(f"  {remedy}")
        sys.exit(1)

    if legacy:
        sess = _legacy_session(
            client, cfg, resolved,
            workspace_dir=workspace_dir,
            interactive=interactive and one_shot is None,
            yolo=yolo,
        )
    else:
        sess = _scoped_session(
            client, cfg, resolved,
            can_prompt=interactive and one_shot is None,
        )

    if one_shot is not None:
        session.run_once(sess, one_shot)
        return

    session.run_repl(sess, browse_catalog=browse_catalog)


def run_scoped_chat(
    client: ChatClient,
    cfg: agent_config.AgentConfig,
    resolved: str,
    *,
    browse_catalog: Callable[[], None] | None = None,
) -> None:
    """Shared scoped-session entry for the TUI chat providers: the caller has
    already built and verified the client, so nothing here re-runs preflight
    or reads configuration. Any provider's client just needs the ChatClient
    shape — the scoped tool surface is identical."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        ui.error("devstuff chat needs a terminal.")
        sys.exit(1)
    sess = _scoped_session(client, cfg, resolved, can_prompt=True)
    session.run_repl(sess, browse_catalog=browse_catalog)


def _legacy_session(
    client: ChatClient,
    cfg: agent_config.AgentConfig,
    resolved: str,
    *,
    workspace_dir: str | None,
    interactive: bool,
    yolo: bool,
) -> session.AgentSession:
    workspace = _select_workspace(workspace_dir, interactive=interactive)

    try:
        tools = registry.build()
    except CatalogError as exc:
        ui.error(str(exc))
        sys.exit(1)

    policy = ApprovalPolicy(
        yolo=yolo,
        auto_approve=cfg.auto_approve,
        can_prompt=interactive,
    )
    return session.AgentSession(
        client,
        cfg,
        workspace,
        model=resolved,
        tools=tools,
        policy=policy,
        transcript=Transcript.create(
            model=resolved, host=cfg.host, workspace=str(workspace.root)
        ),
    )


def _scoped_session(
    client: ChatClient,
    cfg: agent_config.AgentConfig,
    resolved: str,
    *,
    can_prompt: bool,
) -> session.AgentSession:
    from dev_setup.agent.scoped import ScopedActions

    # The workspace object exists because the session type carries one; the
    # scoped toolbox never consults it — no workspace prompt, no warnings.
    workspace = Workspace.create(Path.cwd())
    return session.AgentSession(
        client,
        cfg,
        workspace,
        model=resolved,
        scoped_actions=ScopedActions(can_prompt=can_prompt),
        policy=ApprovalPolicy(can_prompt=False),
        transcript=None,  # scoped sessions persist nothing to disk
    )


@click.command("agent")
@click.option(
    "--workspace", "workspace_mode", is_flag=True,
    help="Legacy workspace agent (shell/filesystem tools in a chosen directory).",
)
@click.option(
    "--dir", "workspace_dir", default=None,
    help="Workspace directory (legacy mode, skips the prompt).",
)
@click.option("--model", default=None, help="Ollama model to use (overrides agent.yaml).")
@click.option("--host", default=None, help="Ollama host URL (overrides agent.yaml).")
@click.option("--print", "one_shot", default=None, help="Run a single prompt and exit.")
@click.option(
    "--yolo",
    is_flag=True,
    help="Legacy workspace mode only: run mutating tools without confirmation.",
)
@click.option("--setup", is_flag=True, help="Run the configuration wizard, then continue.")
def agent_cmd(
    workspace_mode: bool,
    workspace_dir: str | None,
    model: str | None,
    host: str | None,
    one_shot: str | None,
    yolo: bool,
    setup: bool,
) -> None:
    """Chat with the devstuff assistant (catalog-scoped by default)."""
    run_agent(
        model=model,
        host=host,
        workspace_dir=workspace_dir,
        workspace_mode=workspace_mode,
        one_shot=one_shot,
        yolo=yolo,
        setup=setup,
    )
