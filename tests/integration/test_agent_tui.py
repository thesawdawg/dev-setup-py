"""Keystroke tests for the agent prompt, driven through a real PTY.

prompt_toolkit resolves completions in a background task, so a pipe-fed test cannot
exercise Tab: the input is consumed before the completer runs. A PTY with realistic
pauses can. That makes these timing-dependent and slow, so they live here behind the
integration marker rather than in the default suite.

No Ollama daemon is needed -- only the prompt is under test.

    uv run pytest tests/integration/test_agent_tui.py -m integration -v
"""

from __future__ import annotations

import os
import select
import sys
import time

import pytest

pytestmark = pytest.mark.integration

pty = pytest.importorskip("pty", reason="PTY not available on this platform")

_DRIVER = r"""
import sys
from prompt_toolkit.history import InMemoryHistory
from dev_setup.agent import session as sm
from dev_setup.agent.registry import AgentTool

tools = {
    "write_file": AgentTool("write_file", "W", "Write a file.", "primitive", mutating=True),
    "read_file": AgentTool("read_file", "R", "Read a file.", "primitive"),
}
fake = type("S", (), {"tools": tools})()
result = sm.build_prompt_session(fake, history=InMemoryHistory()).prompt("> ")
sys.stderr.write("RESULT:" + repr(result) + "\n")
"""


def drive(keystrokes: list[tuple[str, float]], *, startup: float = 2.5) -> str:
    """Run the prompt in a PTY child, send keystrokes with pauses, return the result."""
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.execv(sys.executable, [sys.executable, "-c", _DRIVER])

    try:
        time.sleep(startup)
        for keys, pause in keystrokes:
            os.write(fd, keys.encode())
            time.sleep(pause)

        output = b""
        deadline = time.time() + 10
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                break
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
    finally:
        os.close(fd)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    text = output.decode(errors="replace")
    marker = [line for line in text.splitlines() if "RESULT:" in line]
    if not marker:
        pytest.fail(f"prompt produced no result. Raw output:\n{text[-2000:]}")
    return marker[-1].split("RESULT:", 1)[1].strip()


def test_tab_completes_a_tool_name():
    result = drive([("/wr", 1.5), ("\t", 1.5), ("\r", 0.6), ("\r", 1.5)])
    assert result == "'/write_file'"


def test_enter_submits_a_single_line():
    assert drive([("hello there", 0.5), ("\r", 1.0)]) == "'hello there'"


def test_alt_enter_inserts_a_newline():
    result = drive([("first", 0.4), ("\x1b\r", 0.4), ("second", 0.4), ("\r", 1.0)])
    assert result == "'first\\nsecond'"
