"""End-to-end smoke test for `devstuff agent`.

Unlike the tool-install integration tests, this one does not run in the Docker CI
image -- it needs a reachable Ollama daemon, which may well be on another machine.
It skips cleanly when there is nothing to talk to, so it is safe to collect
anywhere.

    make -C dev smoke-agent
    DEVSTUFF_AGENT_HOST=http://192.168.1.69:11434 uv run pytest \
        tests/integration/test_agent_smoke.py -m integration -v
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from dev_setup.agent import config as agent_config
from dev_setup.agent import preflight
from dev_setup.agent.ollama import OllamaClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_config():
    """The user's real agent.yaml, with env overrides so another machine can point
    this somewhere else without editing config."""
    cfg = agent_config.load()
    agent_config.apply_overrides(
        cfg,
        model=os.environ.get("DEVSTUFF_AGENT_MODEL"),
        host=os.environ.get("DEVSTUFF_AGENT_HOST"),
    )

    client = OllamaClient(cfg.host, timeout=cfg.request_timeout)
    try:
        preflight.check(cfg, client)
    except preflight.PreflightError as exc:
        pytest.skip(f"no usable Ollama daemon: {exc}")
    return cfg


def _run_agent(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dev_setup", "agent", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_preflight_accepts_the_configured_model(live_config):
    """Config load -> transport -> /api/tags -> /api/show, against a real daemon."""
    client = OllamaClient(live_config.host, timeout=live_config.request_timeout)
    resolved = preflight.check(live_config, client)
    assert resolved.count(":") == 1, "preflight must return a fully-qualified tag"
    assert "tools" in client.capabilities(resolved)


def test_print_mode_answers_a_prompt(live_config):
    """The whole CLI path end to end, as a user would invoke it."""
    result = _run_agent(
        "--host", live_config.host,
        "--model", live_config.model,
        "--print", "Reply with exactly the word: pong",
    )
    assert result.returncode == 0, result.stderr
    assert "pong" in result.stdout.lower()


def test_print_mode_strips_reasoning_from_the_answer(live_config):
    """Regression: some builds ignore think:false and emit <think> tags inline,
    which rendered the model's reasoning as the answer."""
    result = _run_agent(
        "--host", live_config.host,
        "--model", live_config.model,
        "--print", "What is 2+2? Answer with the number only.",
    )
    assert result.returncode == 0, result.stderr
    assert "<think>" not in result.stdout
    assert "4" in result.stdout


def test_unreachable_host_fails_with_guidance():
    """No daemon needed -- this asserts the error path a new user hits first."""
    result = _run_agent("--host", "http://127.0.0.1:1", "--print", "hello", timeout=60)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "Cannot reach Ollama" in combined
    assert "ollama serve" in combined


def test_missing_model_fails_with_guidance(live_config):
    result = _run_agent(
        "--host", live_config.host,
        "--model", "definitely-not-a-real-model:v9",
        "--print", "hello",
        timeout=60,
    )
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "not available locally" in combined
    assert "ollama pull" in combined
