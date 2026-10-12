"""The stdio transport (docs/specs/mcp, FR-11, FR-12, FR-19, FR-30, F-1).

The load-bearing claim is that **nothing but protocol frames ever reaches the protocol channel**.
That is tested by making a fake installer leak through every route F-1 measured, in a real
subprocess whose stdout the test reads — and by running the same leaks *without* the fd move, so
the test is shown to be able to fail.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from dev_setup.agent.registry import AgentParam, AgentTool
from dev_setup.mcp import core, stdio
from dev_setup.mcp.core import Dispatcher, Toolbox

REPO = Path(__file__).resolve().parent.parent


def _env(home: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if home is not None:
        env["HOME"] = str(home)
    return env


# -- the loop, over in-memory pipes -----------------------------------------------------------


def _tool(key, target="list_tools", impl="catalog", mutating=False):
    return AgentTool(key=key, name=key, description=key, impl=impl, target=target, mutating=mutating)


def _install_tool():
    tool = _tool("install_tool", "install_tool")
    tool.params = [AgentParam("key", "string", "a catalog key", required=True)]
    return tool


def _server(runner, *, install=True):
    box = Toolbox.from_agent(
        {"list_tools": _tool("list_tools"), "install_tool": _install_tool()},
        allow_install=install, allow_functions=False, warn=lambda m: None,
    )
    return Dispatcher(box, run=runner, forget=lambda: None, log_error=lambda m: None)


class Pipes:
    """Both ends of a client's stdio, in memory."""

    def __init__(self, dispatcher, **kw):
        c2s_r, self.client_w = os.pipe()
        self.client_r, s2c_w = os.pipe()
        self.rfile = os.fdopen(c2s_r, "rb")
        self.wfile = os.fdopen(s2c_w, "wb", buffering=0)  # as `isolate_stdio` hands it over
        self.result: list[int] = []
        self.thread = threading.Thread(
            target=lambda: self.result.append(stdio.serve(dispatcher, self.rfile, self.wfile, **kw)),
            daemon=True,
        )
        self.thread.start()
        self.reader = os.fdopen(self.client_r, "rb")

    def send(self, obj_or_bytes):
        data = obj_or_bytes if isinstance(obj_or_bytes, bytes) else json.dumps(obj_or_bytes).encode() + b"\n"
        os.write(self.client_w, data)

    def recv(self):
        line = self.reader.readline()
        assert line.endswith(b"\n")
        return json.loads(line)

    def close(self):
        os.close(self.client_w)
        self.thread.join(5)
        assert not self.thread.is_alive()
        return self.result[0]


def ping(i):
    return {"jsonrpc": "2.0", "id": i, "method": "ping"}


def test_frames_in_frames_out_and_eof_ends_with_zero():
    p = Pipes(_server(lambda t, b: "x"))
    p.send(ping(1))
    p.send({"jsonrpc": "2.0", "method": "notifications/initialized"})  # no reply
    p.send(b"\n   \n")  # blank lines are not frames
    p.send(ping(2))
    assert p.recv()["id"] == 1
    assert p.recv()["id"] == 2  # nothing was written for the notification or the blanks
    assert p.close() == 0


def test_a_call_in_flight_at_eof_is_finished_and_answered_not_abandoned():
    """FR-30: killing an installer half-way is worse than waiting for it."""
    started, release = threading.Event(), threading.Event()

    def slow(t, b):
        started.set()
        release.wait(5)
        return "finished"

    p = Pipes(_server(slow))
    p.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "install_tool", "arguments": {"key": "x"}}})
    assert started.wait(2)
    os.close(p.client_w)  # the client goes away mid-install
    time.sleep(0.2)
    assert p.thread.is_alive()  # still waiting for the install
    release.set()
    p.thread.join(5)
    assert not p.thread.is_alive() and p.result == [0]
    assert json.loads(p.reader.readline())["result"]["content"][0]["text"] == "finished"


def test_a_slow_install_does_not_hold_up_a_read_on_the_same_connection():
    started, release = threading.Event(), threading.Event()

    def runner(t, b):
        if t.key == "install_tool":
            started.set()
            release.wait(5)
        return t.key

    p = Pipes(_server(runner))
    p.send({"jsonrpc": "2.0", "id": "install", "method": "tools/call",
            "params": {"name": "install_tool", "arguments": {"key": "x"}}})
    assert started.wait(2)
    p.send({"jsonrpc": "2.0", "id": "read", "method": "tools/call", "params": {"name": "list_tools"}})
    assert p.recv()["id"] == "read"  # answered while the install is still running
    release.set()
    assert p.recv()["id"] == "install"
    assert p.close() == 0


def test_an_unparseable_line_is_answered_and_the_loop_carries_on():
    p = Pipes(_server(lambda t, b: "x"))
    p.send(b"{not json\n")
    assert p.recv()["error"]["code"] == -32700
    p.send(b"\xff\xfe not utf-8\n")
    assert p.recv()["error"]["code"] == -32700
    p.send(ping(3))
    assert p.recv()["id"] == 3
    assert p.close() == 0


def test_an_oversized_frame_is_refused_and_does_not_poison_the_next_one():
    p = Pipes(_server(lambda t, b: "x"), max_frame=64)
    p.send(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"pad":"' + b"a" * 500 + b'"}}\n')
    err = p.recv()
    assert err["error"]["code"] == -32600 and err["id"] is None
    p.send(ping(2))
    assert p.recv()["id"] == 2  # the rest of the long line was discarded, not read as a frame
    assert p.close() == 0


def test_a_client_that_has_gone_away_does_not_crash_the_server():
    p = Pipes(_server(lambda t, b: "x"))
    p.reader.close()  # nobody is listening any more
    p.send(ping(1))
    time.sleep(0.2)  # the reply has nowhere to go: the write fails, and must not be fatal
    assert p.close() == 0  # it carried on until EOF and ended normally


# -- FR-11/12: the protocol channel carries frames and nothing else --------------------------------

LEAKS = """
import os, subprocess, sys
print("leak: print")
sys.stdout.write("leak: sys.stdout\\n"); sys.stdout.flush()
os.write(1, b"leak: os.write\\n")
subprocess.run(["echo", "leak: child echo"])
subprocess.run(["cat"])          # a child that reads stdin must see EOF, not our queued frame
"""


def _run_leaky(tmp_path: Path, *, isolate: bool) -> tuple[bytes, str]:
    body = "import os, sys\nfrom dev_setup.mcp import stdio\n"
    if isolate:
        body += "chan = stdio.isolate_stdio()\n"
    else:
        body += "class _C: pass\nchan = _C(); chan.wfile = os.fdopen(os.dup(1), 'wb')\n"
    body += LEAKS + "chan.wfile.write(b'FRAME\\n'); chan.wfile.flush()\n"
    if isolate:
        # The queued frame must have survived the child that read stdin.
        body += "sys.stderr.write('queued=' + repr(chan.rfile.readline()) + '\\n')\n"
    script = tmp_path / "leaky.py"
    script.write_text(body)
    proc = subprocess.run([sys.executable, str(script)], input=b"QUEUED-FRAME\n", capture_output=True,
                          env=_env(), timeout=30)
    return proc.stdout, proc.stderr.decode()


def test_nothing_but_the_frame_reaches_the_protocol_channel(tmp_path):
    out, err = _run_leaky(tmp_path, isolate=True)
    assert out == b"FRAME\n"
    for leak in ("print", "sys.stdout", "os.write", "child echo"):
        assert f"leak: {leak}" in err  # every route landed on stderr, where a log belongs
    assert "queued=b'QUEUED-FRAME\\n'" in err  # and a child reading stdin did not eat the next frame


def test_without_the_fd_move_the_same_leaks_do_corrupt_the_stream(tmp_path):
    """The control: shows the test above could have failed."""
    out, _ = _run_leaky(tmp_path, isolate=False)
    assert out != b"FRAME\n"
    assert b"leak: os.write" in out and b"leak: child echo" in out and b"leak: print" in out


def test_the_private_descriptors_are_not_inherited_by_children(tmp_path):
    """A child must not be able to write to — or read from — the protocol channel."""
    script = tmp_path / "inherit.py"
    script.write_text(textwrap.dedent("""
        import os, subprocess, sys
        from dev_setup.mcp import stdio
        chan = stdio.isolate_stdio()
        priv = [chan.rfile.fileno(), chan.wfile.fileno()]
        # close_fds=False: the only thing keeping the channel from the child is that the fds are
        # not inheritable. A child that can fstat one of them could write frames to the client.
        probe = (
            "import os,sys\\nfor fd in map(int, sys.argv[1:]):\\n"
            "    try: os.fstat(fd); print('open', fd)\\n    except OSError: print('closed', fd)"
        )
        child = subprocess.run([sys.executable, "-c", probe, *map(str, priv)], close_fds=False,
                               capture_output=True, text=True)
        inheritable = repr([os.get_inheritable(fd) for fd in priv])
        sys.stderr.write(inheritable + child.stdout.replace("\\n", " ") + "\\n")
    """))
    proc = subprocess.run(
        [sys.executable, str(script)], input=b"", capture_output=True, env=_env(), timeout=30
    )
    last = proc.stderr.decode().strip().splitlines()[-1]
    assert last.startswith("[False, False]") and "open" not in last and last.count("closed") == 2


# -- the whole command, as a client runs it ---------------------------------------------------------


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config" / "devstuff").mkdir(parents=True)
    return h


class Server:
    def __init__(self, home: Path, *flags: str):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dev_setup", "mcp", *flags], env=_env(home), cwd=home,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._id = 0

    def request(self, method, params=None):
        self._id += 1
        frame = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            frame["params"] = params
        self.proc.stdin.write(json.dumps(frame).encode() + b"\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, f"server closed stdout: {self.proc.stderr.read().decode()}"
        return json.loads(line)

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}).encode() + b"\n")
        self.proc.stdin.flush()

    def finish(self, timeout=15):
        self.proc.stdin.close()
        rc = self.proc.wait(timeout)
        self.stdout_rest = self.proc.stdout.read()
        self.stderr = self.proc.stderr.read().decode()
        return rc

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture
def server(home):
    made: list[Server] = []

    def start(*flags):
        s = Server(home, *flags)
        made.append(s)
        return s

    yield start
    for s in made:
        s.kill()


def test_a_client_session_end_to_end(server):
    s = server()
    init = s.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "0"}})
    assert init["result"]["protocolVersion"] == "2025-11-25"
    s.notify("notifications/initialized")
    assert s.request("server/discover")["error"]["code"] == -32601
    tools = s.request("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_tools", "outdated", "profile_diff"} <= names
    assert not names & {"install_tool", "read_file", "run_command"}
    assert not any(n.startswith("fn_") for n in names)
    out = s.request("tools/call", {"name": "tool_info", "arguments": {"key": "uv"}})["result"]
    assert out["isError"] is False and "key: uv" in out["content"][0]["text"]
    gated = s.request("tools/call", {"name": "install_tool", "arguments": {"key": "uv"}})
    assert gated["error"]["code"] == -32602
    assert s.finish() == 0
    assert s.stdout_rest == b""  # nothing after the last frame


def test_verbose_output_goes_to_stderr_and_never_into_the_stream(server):
    s = server("-vv")
    s.request("initialize", {"protocolVersion": "2025-11-25"})
    s.request("tools/call", {"name": "tool_info", "arguments": {"key": "uv"}})  # parsed by request(): any
    assert s.finish() == 0                                                      # stray line would raise
    assert "devstuff mcp" in s.stderr


def test_a_function_that_writes_to_stdout_is_a_result_not_a_corrupt_frame(server, home):
    (home / ".config" / "devstuff" / "functions.yaml").write_text(textwrap.dedent("""
        version: 1
        functions:
          chatty:
            name: Chatty
            description: writes a lot
            type: script
            script: |
              echo "to stdout"
              echo "to stderr" >&2
              exit 0
    """))
    s = server("--allow-functions")
    s.request("initialize", {"protocolVersion": "2025-11-25"})
    out = s.request("tools/call", {"name": "fn_chatty"})["result"]
    assert "to stdout" in out["content"][0]["text"]
    assert s.finish() == 0 and s.stdout_rest == b""


def _wait_started(home: Path, timeout: float = 20.0) -> None:
    """Block until the slow function has actually begun, instead of guessing with a sleep."""
    deadline = time.monotonic() + timeout
    while not (home / "started").exists():
        assert time.monotonic() < deadline, "the function never started"
        time.sleep(0.02)


SLOW = textwrap.dedent("""
    version: 1
    functions:
      slow:
        name: Slow
        description: takes a while
        type: script
        script: |
          touch "$HOME/started"
          sleep 1.5
          echo finished
          exit 0
""")


def test_a_signal_does_not_abort_a_running_call_but_stops_new_ones(server, home):
    """FR-30: wait for the running call, answer it, then exit 0."""
    (home / ".config" / "devstuff" / "functions.yaml").write_text(SLOW)
    s = server("--allow-functions")
    s.request("initialize", {"protocolVersion": "2025-11-25"})
    s.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                   "params": {"name": "fn_slow"}}).encode() + b"\n")
    s.proc.stdin.flush()
    _wait_started(home)  # the script is running now, so the signal arrives mid-call
    s.proc.send_signal(signal.SIGTERM)
    reply = json.loads(s.proc.stdout.readline())
    assert reply["id"] == 9 and "finished" in reply["result"]["content"][0]["text"]
    assert s.proc.wait(15) == 0


def test_a_second_signal_exits_immediately(server, home):
    (home / ".config" / "devstuff" / "functions.yaml").write_text(SLOW.replace("1.5", "30"))
    s = server("--allow-functions")
    s.request("initialize", {"protocolVersion": "2025-11-25"})
    s.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                   "params": {"name": "fn_slow"}}).encode() + b"\n")
    s.proc.stdin.flush()
    _wait_started(home)
    s.proc.send_signal(signal.SIGTERM)
    time.sleep(0.3)
    assert s.proc.poll() is None  # the first one waits for the call
    started = time.monotonic()
    s.proc.send_signal(signal.SIGTERM)
    s.proc.wait(10)
    assert time.monotonic() - started < 5


def test_sigint_on_an_idle_server_exits_zero(server):
    s = server()
    s.request("initialize", {"protocolVersion": "2025-11-25"})
    s.proc.send_signal(signal.SIGINT)
    assert s.proc.wait(10) == 0


def test_the_server_has_no_controlling_terminal_of_its_own(server):
    """It reaches /dev/tty only if the detach failed — and with no gate flag that is a warning."""
    s = server()
    s.request("initialize", {"protocolVersion": "2025-11-25"})
    assert s.finish() == 0
    assert "attached to a controlling terminal" not in s.stderr


def test_list_tools_prints_what_would_be_exposed_and_exits_without_serving(home):
    def run(*flags):
        return subprocess.run(
            [sys.executable, "-m", "dev_setup", "mcp", "--list-tools", *flags], env=_env(home),
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )

    plain = run()
    assert plain.returncode == 0
    assert "list_tools" in plain.stdout and "install_tool" not in plain.stdout
    both = run("--allow-install", "--allow-functions")
    assert "install_tool" in both.stdout and "fn_validate_yaml" in both.stdout
    listed = {line.split()[0] for line in both.stdout.splitlines()}  # descriptions may *mention* them
    assert not listed & {"read_file", "write_file", "run_command", "cd", "list_dir"}
    assert core.Gate.INSTALL.value in both.stdout  # each tool says which gate it sits behind


def test_a_gate_flag_with_a_reachable_terminal_refuses_to_start(home, monkeypatch):
    """The refusal path cannot be reached by a real run on a machine where detaching works, so the
    command is driven in-process with the detach forced to fail (FR-11a.3)."""
    from click.testing import CliRunner

    from dev_setup.commands import mcp_cmd as mod
    from dev_setup.mcp import process

    monkeypatch.setattr(process, "detach", lambda: "setsid")
    monkeypatch.setattr(process, "terminal_reachable", lambda: True)
    monkeypatch.setattr(stdio, "run", lambda *a, **k: pytest.fail("must not start anything"))
    result = CliRunner().invoke(mod.mcp_cmd, ["--allow-install"])
    assert result.exit_code == 1
    assert "refusing to start" in result.output

    started = []
    monkeypatch.setattr(stdio, "run", lambda *a, **k: started.append(1) or 0)
    ok = CliRunner().invoke(mod.mcp_cmd, [])  # read-only: a warning, then it goes on
    assert ok.exit_code == 0 and started == [1]
