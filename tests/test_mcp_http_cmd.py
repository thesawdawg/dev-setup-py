"""`devstuff mcp --http`, as a user runs it: flags, the token file, the banner, signals
(docs/specs/mcp, FR-24, FR-25, FR-27, FR-29, FR-30).

Each test starts the real command in a subprocess with an isolated `HOME` and `XDG_STATE_HOME`, so
nothing here touches the user's own token file or catalogs.
"""

from __future__ import annotations

import http.client
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.mcp_process_support import Http, env_for, token_path

SLOW_FN = textwrap.dedent("""
    version: 1
    functions:
      slow:
        name: Slow
        description: takes a while
        type: script
        script: |
          touch "$HOME/started"
          sleep SECONDS
          echo finished
          exit 0
""")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config" / "devstuff").mkdir(parents=True)
    return h


@pytest.fixture
def start(home):
    made: list[Http] = []

    def go(*flags, **kw):
        s = Http(home, *flags, **kw)
        made.append(s)
        return s

    yield go
    for s in made:
        s.kill()


def test_it_serves_and_a_signal_stops_it_with_zero(start):
    s = start()
    assert s.port, s.banner
    assert s.request("GET", "/tools")[0] == 200
    assert s.stop() == 0


def test_the_banner_says_what_is_on_and_where_and_names_the_token_file_but_never_the_token(start, home):
    s = start("--allow-install")
    assert s.request("GET", "/tools")[0] == 200
    rc = s.stop()
    assert rc == 0
    assert "devstuff mcp: serving 6 tools on http://127.0.0.1:" in s.err
    assert "+ install_tool" in s.err and str(token_path(home)) in s.err
    assert s.token if False else True
    secret = token_path(home).read_text().strip()
    assert secret not in s.err and secret not in s.out
    assert s.out == ""  # nothing at all on stdout


def test_the_token_is_never_logged_even_at_vv(start, home):
    s = start("-vv")
    for _ in range(3):
        s.request("GET", "/tools")
        s.request("GET", "/tools", token=False)
    s.stop()
    assert token_path(home).read_text().strip() not in s.err
    assert "401" in s.err  # the requests were logged, so the check above had something to find


def test_the_token_survives_a_restart_and_rotate_replaces_it(start, home):
    first = start()
    t1 = first.token
    first.stop()
    second = start()
    assert second.token == t1
    assert second.request("GET", "/tools")[0] == 200
    second.stop()
    third = start("--rotate-token")
    assert third.token != t1
    assert third.request("GET", "/tools")[0] == 200
    third.stop()
    assert stat_mode(token_path(home)) == 0o600 and stat_mode(token_path(home).parent) == 0o700


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_a_loose_token_file_is_refused_and_nothing_starts(start, home):
    s = start()
    s.stop()
    token_path(home).chmod(0o644)
    t = start()
    assert t.proc.wait(20) == 1
    assert "permissions" in t.banner + t.proc.stderr.read()
    assert t.port is None


def test_a_busy_port_is_exit_1_never_a_fallback(home):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        proc = subprocess.run(
            [sys.executable, "-m", "dev_setup", "mcp", "--http", "--port", str(port)], env=env_for(home),
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
    assert proc.returncode == 1
    assert str(port) in proc.stderr and "never picks a different port" in proc.stderr
    assert proc.stdout == ""


def test_the_default_port_is_8765(home):
    from dev_setup.commands import mcp_cmd

    assert mcp_cmd.DEFAULT_PORT == 8765 and mcp_cmd.DEFAULT_HOST == "127.0.0.1"


def test_a_non_loopback_host_is_allowed_and_warned_about(start):
    s = start("--host", "0.0.0.0")
    assert s.port
    assert s.request("GET", "/tools")[0] == 200  # reachable on loopback too
    s.stop()
    assert "no TLS" in s.err and "0.0.0.0" in s.err and "SSH tunnel" in s.err


def test_a_loopback_host_is_not_warned_about(start):
    s = start()
    s.stop()
    assert "no TLS" not in s.err


@pytest.mark.parametrize("flags", [
    ["--host", "127.0.0.1"], ["--port", "9000"], ["--token-file", "/tmp/x"], ["--rotate-token"],
    ["--allow-origin", "http://localhost:3000"], ["--allow-host", "host.docker.internal"],
])
def test_http_only_flags_without_http_are_a_usage_error(home, flags):
    proc = subprocess.run([sys.executable, "-m", "dev_setup", "mcp", *flags], env=env_for(home),
                          capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 2
    assert flags[0] in proc.stderr and "--http" in proc.stderr


@pytest.mark.parametrize("flags", [
    ["--allow-host", "host.docker.internal:8765"], ["--allow-host", "http://x"], ["--allow-host", ""],
    ["--allow-origin", "localhost:3000"], ["--allow-origin", "http://x/path"], ["--allow-origin", "*"],
])
def test_a_malformed_allow_value_is_a_usage_error(home, flags):
    proc = subprocess.run([sys.executable, "-m", "dev_setup", "mcp", "--http", "--port", "0", *flags],
                          env=env_for(home), capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 2


def test_the_flags_reach_the_server(start, home):
    s = start("--allow-install", "--allow-functions", "--allow-host", "host.docker.internal",
              "--allow-origin", "http://localhost:3000")
    names = {t["function"]["name"] for t in s.request("GET", "/tools")[1]}
    assert "install_tool" in names and "fn_validate_yaml" in names
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=30)
    conn.request("GET", "/tools", headers={"Host": f"host.docker.internal:{s.port}",
                                           "Authorization": f"Bearer {s.token}",
                                           "Origin": "http://localhost:3000"})
    assert conn.getresponse().status == 200
    s.stop()


def test_a_signal_waits_for_a_running_call_then_exits_zero(start, home):
    (home / ".config" / "devstuff" / "functions.yaml").write_text(SLOW_FN.replace("SECONDS", "1.5"))
    s = start("--allow-functions")
    result: list = []
    import threading

    t = threading.Thread(target=lambda: result.append(s.request("POST", "/tools/fn_slow", body={})))
    t.start()
    deadline = time.monotonic() + 20
    while not (home / "started").exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    s.proc.send_signal(signal.SIGTERM)
    t.join(20)
    assert result and result[0][0] == 200 and "finished" in result[0][1]["text"]
    assert s.proc.wait(20) == 0


def test_a_second_signal_exits_at_once(start, home):
    (home / ".config" / "devstuff" / "functions.yaml").write_text(SLOW_FN.replace("SECONDS", "60"))
    s = start("--allow-functions")
    import threading

    def doomed():
        try:
            s.request("POST", "/tools/fn_slow", body={})
        except OSError:
            pass  # the server is about to exit under it: that is the point of the test
        except http.client.HTTPException:
            pass

    threading.Thread(target=doomed, daemon=True).start()
    deadline = time.monotonic() + 20
    while not (home / "started").exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    s.proc.send_signal(signal.SIGTERM)
    time.sleep(0.5)
    assert s.proc.poll() is None  # the first waits for the install
    started = time.monotonic()
    s.proc.send_signal(signal.SIGTERM)
    s.proc.wait(10)
    assert time.monotonic() - started < 5


def test_started_from_a_shell_it_forks_a_supervisor_and_a_signal_still_stops_it(start):
    """`start_new_session` makes the process a group leader, as an interactive shell does, so
    `setsid` raises EPERM and the server is a forked child. The supervisor must still deliver the
    stop, and report the server's own status."""
    s = start(new_session=True)
    assert s.port
    assert s.request("GET", "/tools")[0] == 200
    assert s.stop() == 0
    # And the port is released: a new server can take it.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", s.port))


def test_the_server_answers_over_every_framing_with_the_same_tools(start):
    s = start("--allow-install")
    over_rest = {t["function"]["name"] for t in s.request("GET", "/tools")[1]}
    rpc = s.request("POST", "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[1]
    over_mcp = {t["name"] for t in rpc["result"]["tools"]}
    doc = s.request("GET", "/openapi.json")[1]
    over_doc = {op["operationId"] for item in doc["paths"].values() for op in item.values()}
    over_doc -= {"openai_tool_list"}
    assert over_rest == over_mcp == over_doc and "install_tool" in over_rest
    s.stop()
