"""G-1: the real Claude Code client connects to `devstuff mcp` (docs/specs/mcp, F-8, §6).

`claude mcp list` performs a real handshake with every configured server — `server/discover`, then
`initialize`, `notifications/initialized`, `tools/list` — so a `✔ Connected` line is the product we care
most about agreeing with us, not an inference from the protocol text. No model turn is made and no
credentials are used.

Skipped, with the reason shown, when `claude` is not on `PATH` (it is not in CI). Not marked
`integration`: it installs nothing and needs neither sudo nor the network, so it belongs in the ordinary
run on any machine that has the client. Everything happens under an isolated `HOME`, so the user's own
Claude configuration and token file are never read or written.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from tests.mcp_process_support import Http, env_for, token_path

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="`claude` is not on PATH")

TIMEOUT = 180


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config" / "devstuff").mkdir(parents=True)
    return h


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    return p


@pytest.fixture
def env(home):
    return {**env_for(home), "DISABLE_AUTOUPDATER": "1"}


def claude(env, cwd, *args):
    return subprocess.run(
        ["claude", *args], env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=TIMEOUT,
    )


def shell(env, cwd, command):
    """Run a snippet exactly as a person pastes it: through a shell, `$(cat …)` and all."""
    return subprocess.run(
        ["bash", "-c", command], env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=TIMEOUT,
    )


def health(env, cwd, name="devstuff") -> str:
    """The health line `claude mcp list` prints for one server, or fail with everything it printed."""
    result = claude(env, cwd, "mcp", "list")
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith(f"{name}:")]
    assert len(lines) == 1, f"no single line for {name!r}:\n{result.stdout}\n{result.stderr}"
    return lines[0]


def print_config(env, cwd, *flags) -> str:
    """What `devstuff mcp --print-config` writes to stdout — through the real command, in a process."""
    result = subprocess.run(
        [sys.executable, "-m", "dev_setup", "mcp", *flags], env=env, cwd=cwd,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def server(home):
    made: list[Http] = []

    def go(*flags):
        s = Http(home, *flags)
        assert s.port, s.banner
        made.append(s)
        return s

    yield go
    for s in made:
        s.kill()


# -- stdio -----------------------------------------------------------------------------------------


def test_claude_connects_to_devstuff_over_stdio(env, project):
    added = claude(env, project, "mcp", "add", "devstuff", "--", sys.executable, "-m", "dev_setup", "mcp")
    assert added.returncode == 0, added.stderr
    assert "✔ Connected" in health(env, project)


def test_the_printed_stdio_snippet_connects_as_printed(env, project):
    snippet = print_config(env, project, "--print-config", "claude-code")
    assert shell(env, project, snippet).returncode == 0
    assert "✔ Connected" in health(env, project)


def test_the_printed_stdio_snippet_with_the_gate_flags_connects_as_printed(env, project):
    flags = ("--allow-install", "--allow-functions", "--print-config", "claude-code")
    snippet = print_config(env, project, *flags)
    assert "--allow-install --allow-functions" in snippet
    assert shell(env, project, snippet).returncode == 0
    assert "✔ Connected" in health(env, project)


# -- HTTP ------------------------------------------------------------------------------------------


def test_claude_connects_to_devstuff_over_http_with_the_bearer_header(env, project, server):
    s = server()
    added = claude(
        env, project, "mcp", "add", "--transport", "http", "devstuff", f"http://127.0.0.1:{s.port}/mcp",
        "--header", f"Authorization: Bearer {s.token}",
    )
    assert added.returncode == 0, added.stderr
    assert "✔ Connected" in health(env, project)


def test_the_printed_http_snippet_connects_as_printed(env, project, server, home):
    s = server()
    snippet = print_config(env, project, "--http", "--port", str(s.port), "--print-config", "claude-code")
    assert s.token not in snippet and str(token_path(home)) in snippet  # a path, never the secret
    assert shell(env, project, snippet).returncode == 0
    assert "✔ Connected" in health(env, project)


# -- controls: the test above can fail ---------------------------------------------------------------


def test_a_wrong_token_is_reported_as_a_failure_not_a_connection(env, project, server):
    """If `health` could not tell success from failure, every test above would pass whatever the
    server did. Claude says why: the 401 body is ours."""
    s = server()
    claude(
        env, project, "mcp", "add", "--transport", "http", "devstuff", f"http://127.0.0.1:{s.port}/mcp",
        "--header", "Authorization: Bearer not-the-token-not-the-token-1234",
    )
    line = health(env, project)
    assert "✔ Connected" not in line and "Failed to connect" in line and "401" in line


def test_a_command_that_does_not_exist_is_reported_as_a_failure(env, project):
    claude(env, project, "mcp", "add", "devstuff", "--", "/nonexistent/devstuff", "mcp")
    line = health(env, project)
    assert "✔ Connected" not in line and "Failed to connect" in line


def test_the_claude_desktop_snippet_is_json_with_an_absolute_command(env, project):
    """Desktop is not available here, so only the shape is checked; the stdio path it uses is the one
    Claude Code exercises above."""
    data = json.loads(print_config(env, project, "--print-config", "claude-desktop"))
    entry = data["mcpServers"]["devstuff"]
    assert entry["command"].startswith("/") and "mcp" in entry["args"]
