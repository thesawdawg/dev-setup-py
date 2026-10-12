"""The MCP core: toolbox, gating at dispatch, JSON-RPC, serialisation and probe freshness
(docs/specs/mcp, FR-1–8a, FR-13, FR-15–18).

Everything here is driven with hand-built frames and a fake runner: no sockets, no processes, no
installers. That is what `core.py` being pure buys (the same reason as `profile.py`).
"""

from __future__ import annotations

import json
import threading

import pytest

from dev_setup.agent import bridges
from dev_setup.agent import registry as agent_registry
from dev_setup.agent.registry import AgentParam, AgentTool
from dev_setup.agent.sandbox import SandboxError
from dev_setup.mcp import core
from dev_setup.mcp.core import Dispatcher, Toolbox

NAME_RE = r"^[a-zA-Z0-9_-]{1,64}$"


def tool(key, *, impl="catalog", target=None, params=(), mutating=False):
    return AgentTool(
        key=key, name=key.title(), description=f"the {key} tool", impl=impl,
        target=target or key, mutating=mutating, params=list(params),
    )


KEY = AgentParam("key", "string", "a catalog key", required=True)

TOOLS = {
    "list_tools": tool("list_tools"),
    "echo": tool("echo", target="tool_info", params=[KEY]),
    "install_tool": tool("install_tool", params=[KEY]),
    "fn_x": tool("fn_x", impl="function", target="x", mutating=True),
    # None of these may reach a client:
    "read_file": tool("read_file", impl="primitive"),
    "weird": tool("weird", target="not_a_bridge"),
    "bad name!": tool("bad name!", target="list_tools"),
    "openai_tool_list": tool("openai_tool_list", target="list_tools"),
}


def toolbox(*, install=False, functions=False, tools=None, warns=None):
    return Toolbox.from_agent(
        TOOLS if tools is None else tools,
        allow_install=install,
        allow_functions=functions,
        warn=(warns.append if warns is not None else (lambda m: None)),
    )


class Runner:
    """A fake for `core._invoke`: records calls, can block, can raise."""

    def __init__(self, replies=None):
        self.calls: list[tuple[str, dict]] = []
        self.replies = replies or {}

    def __call__(self, t, bound):
        self.calls.append((t.key, bound))
        reply = self.replies.get(t.key, "ok")
        return reply(t, bound) if callable(reply) else reply


def dispatcher(*, install=False, functions=False, runner=None, forget=None, errors=None, **kw):
    return Dispatcher(
        toolbox(install=install, functions=functions, **kw),
        run=runner or Runner(),
        forget=forget or (lambda: None),
        log_error=(errors.append if errors is not None else (lambda m: None)),
    )


def rpc(d, method, params=None, **kw):
    frame = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        frame["params"] = params
    frame.update(kw if kw else {"id": 1})
    return d.handle(frame)


def call(d, name, arguments=None, **kw):
    return rpc(d, "tools/call", {"name": name, "arguments": arguments or {}}, **kw)


# -- the toolbox: FR-1, FR-1a, FR-16b, FR-16c ---------------------------------------------


@pytest.mark.parametrize(
    ("install", "functions", "expected"),
    [
        (False, False, {"list_tools", "echo"}),
        (True, False, {"list_tools", "echo", "install_tool"}),
        (False, True, {"list_tools", "echo", "fn_x"}),
        (True, True, {"list_tools", "echo", "install_tool", "fn_x"}),
    ],
)
def test_exposure_follows_the_flags(install, functions, expected):
    assert set(toolbox(install=install, functions=functions).names()) == expected


def test_a_tool_that_cannot_be_exposed_is_left_out_with_a_warning_not_an_error():
    warns: list[str] = []
    toolbox(install=True, functions=True, warns=warns)
    joined = "\n".join(warns)
    assert "weird" in joined  # unclassified: fail closed, and say so
    assert "bad name!" in joined  # FR-16b
    assert "openai_tool_list" in joined  # reserved (FR-22)
    assert "read_file" not in joined  # a primitive is expected to be absent: not worth a warning


def test_every_bundled_tool_name_is_a_legal_client_tool_name():
    """FR-16b: Claude prefixes names and applies a pattern; a bad one would break `tools/list`."""
    import re

    box = Toolbox.from_agent(
        agent_registry.build(), allow_install=True, allow_functions=True, warn=lambda m: pytest.fail(m)
    )
    assert box.names()
    assert all(re.fullmatch(NAME_RE, n) for n in box.names())


def test_input_schema_omits_an_empty_required_and_forbids_extra_properties():
    schemas = {t["name"]: t["inputSchema"] for t in toolbox().describe()}
    assert schemas["list_tools"] == {"type": "object", "properties": {}, "additionalProperties": False}
    assert "required" not in schemas["list_tools"]
    assert schemas["echo"]["required"] == ["key"]
    assert schemas["echo"]["additionalProperties"] is False


def test_described_tool_has_the_fields_a_client_reads():
    t = next(t for t in toolbox().describe() if t["name"] == "echo")
    assert set(t) == {"name", "title", "description", "inputSchema"}
    assert t["description"] == "the echo tool"


# -- JSON-RPC: FR-15, FR-16, FR-16a, FR-17, FR-18 ------------------------------------------


def test_initialize_negotiates_and_advertises_tools_only():
    reply = rpc(dispatcher(), "initialize", {"protocolVersion": "2025-11-25"})
    assert reply["result"]["protocolVersion"] == "2025-11-25"
    assert reply["result"]["capabilities"] == {"tools": {}}
    assert reply["result"]["serverInfo"]["name"] == "devstuff"


def test_initialize_with_a_version_it_does_not_know_answers_with_its_own():
    reply = rpc(dispatcher(), "initialize", {"protocolVersion": "2099-01-01"})
    assert "error" not in reply  # never refuses a session over a version
    assert reply["result"]["protocolVersion"] == core.PROTOCOL_VERSIONS[0]


def test_initialize_without_params_still_answers():
    assert "result" in rpc(dispatcher(), "initialize")


@pytest.mark.parametrize("rid", [0, "", "server-discover-probe-1", 7])
def test_the_id_is_echoed_with_its_type(rid):
    """FR-16a: Claude Code sends an integer 0 and a string id. A falsy id is still a request."""
    reply = rpc(dispatcher(), "ping", id=rid)
    assert reply["id"] == rid and type(reply["id"]) is type(rid)
    assert reply["result"] == {}


def test_a_request_with_a_null_id_is_still_answered():
    assert rpc(dispatcher(), "ping", id=None)["id"] is None


def test_a_notification_is_the_absence_of_the_id_key_and_gets_no_reply():
    d = dispatcher()
    assert d.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert d.handle({"jsonrpc": "2.0", "method": "no/such/thing"}) is None  # even an unknown one


def test_an_unknown_method_is_minus_32601_not_an_empty_result():
    """FR-16: JSON-RPC's answer to an unimplemented method. The real clients would also tolerate `{}`
    (F-16), so nothing but this test and the raw-frame one in the conformance suite holds the line."""
    for method in ("server/discover", "resources/list", "prompts/list", "completion/complete"):
        reply = rpc(dispatcher(), method)
        assert reply["error"]["code"] == -32601 and "result" not in reply


@pytest.mark.parametrize("frame", [[], [{"jsonrpc": "2.0", "method": "ping", "id": 1}], 3, "x", None])
def test_a_batch_or_a_non_object_is_minus_32600_with_a_null_id(frame):
    reply = dispatcher().handle(frame)
    assert reply["error"]["code"] == -32600 and reply["id"] is None


def test_a_request_with_no_method_is_minus_32600():
    assert dispatcher().handle({"jsonrpc": "2.0", "id": 4})["error"]["code"] == -32600
    assert dispatcher().handle({"jsonrpc": "2.0", "id": 4, "method": 9})["error"]["code"] == -32600


def test_unparseable_text_is_minus_32700_with_a_null_id():
    reply = json.loads(dispatcher().handle_text("{not json"))
    assert reply["error"]["code"] == -32700 and reply["id"] is None


def test_handle_text_round_trips_and_stays_on_one_line():
    out = dispatcher().handle_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}))
    assert "\n" not in out
    assert json.loads(out) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert dispatcher().handle_text(json.dumps({"jsonrpc": "2.0", "method": "notifications/x"})) is None


def test_a_tool_result_containing_a_newline_still_serialises_to_one_line():
    """FR-19: a frame may not contain a raw newline, and tool text is full of them."""
    d = dispatcher(runner=Runner({"list_tools": "a\nb\r\nc d"}))
    out = d.handle_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "list_tools"}}))
    assert "\n" not in out and "\r" not in out
    assert json.loads(out)["result"]["content"][0]["text"] == "a\nb\r\nc d"


def test_tools_list_returns_only_what_is_exposed():
    listed = rpc(dispatcher(), "tools/list")["result"]["tools"]
    assert {t["name"] for t in listed} == {"list_tools", "echo"}


# -- tools/call: FR-3, FR-5, FR-6, FR-7 ----------------------------------------------------


def test_a_call_returns_one_text_item_with_the_bridges_string_unchanged():
    reply = call(dispatcher(runner=Runner({"list_tools": "  exact\ttext\n"})), "list_tools")
    assert reply["result"] == {"content": [{"type": "text", "text": "  exact\ttext\n"}], "isError": False}


@pytest.mark.parametrize(("install", "functions", "name"), [
    (False, False, "install_tool"), (False, True, "install_tool"),
    (False, False, "fn_x"), (True, False, "fn_x"),
])
def test_a_gated_tool_is_absent_refused_and_never_run(install, functions, name):
    """FR-3 is three assertions, because a list-only check passes when dispatch is wide open."""
    runner = Runner()
    d = dispatcher(install=install, functions=functions, runner=runner)
    assert name not in {t["name"] for t in rpc(d, "tools/list")["result"]["tools"]}
    reply = call(d, name, {"key": "uv"})
    unknown = call(d, "no_such_tool", {"key": "uv"})
    # Refused exactly as an unknown tool is: same code, same wording, nothing to tell a probing
    # client that the name exists behind a flag.
    assert reply["error"]["code"] == unknown["error"]["code"] == -32602
    gated_msg = reply["error"]["message"].replace(name, "X")
    assert gated_msg == unknown["error"]["message"].replace("no_such_tool", "X")
    assert runner.calls == []


@pytest.mark.parametrize("name", ["read_file", "weird", "bad name!", "openai_tool_list", "nope"])
def test_a_tool_that_was_left_out_cannot_be_called_whatever_the_flags(name):
    runner = Runner()
    d = dispatcher(install=True, functions=True, runner=runner)
    assert call(d, name)["error"]["code"] == -32602
    assert runner.calls == []


@pytest.mark.parametrize("params", [None, [], "x", {}, {"name": 3}, {"name": "list_tools", "arguments": []},
                                    {"name": "list_tools", "arguments": "x"}])
def test_malformed_call_params_are_minus_32602(params):
    assert rpc(dispatcher(), "tools/call", params)["error"]["code"] == -32602


def test_omitted_arguments_mean_none_and_null_means_none():
    runner = Runner()
    d = dispatcher(runner=runner)
    assert rpc(d, "tools/call", {"name": "list_tools"})["result"]["isError"] is False
    assert rpc(d, "tools/call", {"name": "list_tools", "arguments": None})["result"]["isError"] is False


def test_bad_arguments_are_a_tool_error_the_model_can_fix_never_a_protocol_error():
    d = dispatcher()
    missing = call(d, "echo", {})["result"]
    assert missing["isError"] is True and "key" in missing["content"][0]["text"]
    extra = call(d, "list_tools", {"bogus": 1})["result"]
    assert extra["isError"] is True and "bogus" in extra["content"][0]["text"]


def test_a_bridge_that_raises_sandbox_error_is_a_tool_error_with_its_message():
    def boom(t, b):
        raise SandboxError("unknown catalog tool 'zz'")

    out = call(dispatcher(runner=Runner({"echo": boom})), "echo", {"key": "zz"})["result"]
    assert out == {"content": [{"type": "text", "text": "unknown catalog tool 'zz'"}], "isError": True}


def test_an_unexpected_exception_is_a_one_line_error_and_the_traceback_goes_to_the_log():
    def boom(t, b):
        raise ZeroDivisionError("line one\nline two")

    logged: list[str] = []
    d = dispatcher(runner=Runner({"list_tools": boom}), errors=logged)
    out = call(d, "list_tools")["result"]
    assert out["isError"] is True
    text = out["content"][0]["text"]
    assert "\n" not in text and "ZeroDivisionError" in text and "Traceback" not in text
    assert any("Traceback" in m and "line two" in m for m in logged)
    assert rpc(d, "ping")["result"] == {}  # and the server is still serving


def test_a_bridge_that_calls_sys_exit_does_not_kill_the_request_thread_silently():
    def leave(t, b):
        raise SystemExit(3)

    out = call(dispatcher(runner=Runner({"list_tools": leave})), "list_tools")["result"]
    assert out["isError"] is True


def test_a_note_is_a_second_text_item_so_a_json_result_stays_parseable():
    """FR-7: the advisory note must not be appended to the array it is about."""
    result = core.ToolResult(False, '[{"key": "uv"}]', note="an install is in progress; this may change")
    content = core.to_mcp_result(result)["content"]
    assert json.loads(content[0]["text"]) == [{"key": "uv"}]
    assert content[1] == {"type": "text", "text": "an install is in progress; this may change"}
    assert len(core.to_mcp_result(core.ToolResult(False, "x"))["content"]) == 1


# -- FR-13: an installer that needs a password ----------------------------------------------


def test_an_install_failure_tells_the_client_what_to_do_whether_or_not_sudos_line_survived():
    def failing(t, b):
        raise bridges.InstallFailed("installing docker failed: exit code 1")

    d = dispatcher(install=True, runner=Runner({"install_tool": failing}))
    text = call(d, "install_tool", {"key": "docker"})["result"]["content"][0]["text"]
    assert "exit code 1" in text
    assert "devstuff install docker" in text and "terminal" in text


def test_the_install_hint_is_not_added_to_errors_that_are_not_install_failures():
    def unknown(t, b):
        raise SandboxError("unknown catalog tool 'zz'; try search_catalog first")

    d = dispatcher(install=True, runner=Runner({"install_tool": unknown}))
    text = call(d, "install_tool", {"key": "zz"})["result"]["content"][0]["text"]
    assert "devstuff install" not in text


def test_the_install_bridge_raises_install_failed_for_a_failing_installer(monkeypatch):
    from dev_setup import registry as tool_registry

    class T:
        key, name, requires = "docker", "Docker", []
        is_installed = staticmethod(lambda: False)

        @staticmethod
        def install():
            raise RuntimeError("sudo: A terminal is required to authenticate")

    monkeypatch.setattr(tool_registry, "init", lambda: None)
    monkeypatch.setattr(tool_registry, "get", lambda k: T)
    monkeypatch.setattr(tool_registry, "missing_requires", lambda t: [])
    with pytest.raises(bridges.InstallFailed, match="A terminal is required"):
        bridges.install_tool(None, None, {"key": "docker"})
    # ...and it is still a SandboxError, so the local agent's handling is unchanged.
    assert issubclass(bridges.InstallFailed, SandboxError)


# -- FR-8: serialisation --------------------------------------------------------------------


class Latch:
    """Lets a test hold a fake tool call open and see who else gets through."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()


def _thread(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


def test_two_gated_calls_never_overlap():
    running, peak, lock = [0], [0], threading.Lock()
    hold = Latch()

    def slow(t, b):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        hold.entered.set()
        hold.release.wait(2)
        with lock:
            running[0] -= 1
        return "done"

    d = dispatcher(install=True, functions=True, runner=Runner({"install_tool": slow, "fn_x": slow}))
    first = _thread(lambda: call(d, "install_tool", {"key": "a"}))
    assert hold.entered.wait(2)
    second = _thread(lambda: call(d, "fn_x"))  # a different gated tool: one lock for all of them
    second.join(0.3)
    assert second.is_alive()  # waiting, not running
    hold.release.set()
    first.join(2)
    second.join(2)
    assert peak[0] == 1


def test_a_read_is_never_blocked_by_a_running_mutation():
    hold = Latch()

    def slow(t, b):
        hold.entered.set()
        hold.release.wait(5)
        return "installed"

    d = dispatcher(install=True, runner=Runner({"install_tool": slow}))
    m = _thread(lambda: call(d, "install_tool", {"key": "a"}))
    assert hold.entered.wait(2)
    done = []
    r = _thread(lambda: done.append(call(d, "list_tools")))
    r.join(2)
    assert done and done[0]["result"]["isError"] is False  # answered while the install was running
    hold.release.set()
    m.join(2)


# -- FR-8a: probe freshness -------------------------------------------------------------------


class Forgets:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1


def test_a_read_after_an_idle_gap_starts_from_clean_probes():
    f = Forgets()
    d = dispatcher(forget=f)
    call(d, "list_tools")
    call(d, "list_tools")
    assert f.n == 2  # one per burst: nothing is kept across idle time


def test_reads_in_one_burst_share_probes():
    f = Forgets()
    both_in = threading.Barrier(2, timeout=2)

    def read(t, b):
        both_in.wait()  # both are in flight at once
        return "ok"

    d = dispatcher(forget=f, runner=Runner({"list_tools": read}))
    a = _thread(lambda: call(d, "list_tools"))
    b = _thread(lambda: call(d, "list_tools"))
    a.join(3)
    b.join(3)
    assert f.n == 1  # the 0->1 transition cleared once; the second joined the burst


def test_probes_are_cleared_after_a_mutation_even_when_it_fails():
    f = Forgets()

    def boom(t, b):
        raise bridges.InstallFailed("installing x failed: half done")

    d = dispatcher(install=True, forget=f, runner=Runner({"install_tool": boom}))
    call(d, "install_tool", {"key": "x"})
    assert f.n == 1  # in `finally`: a partial failure may have changed the machine


def test_probes_are_cleared_after_a_mutation_that_raises_something_unexpected():
    f = Forgets()

    def boom(t, b):
        raise MemoryError

    d = dispatcher(install=True, forget=f, runner=Runner({"install_tool": boom}))
    call(d, "install_tool", {"key": "x"})
    assert f.n == 1


def test_a_read_that_straddled_a_finished_mutation_is_run_again_on_fresh_probes():
    f = Forgets()
    seen: list[str] = []

    def read(t, b):
        if not seen:
            seen.append("stale")
            call(d, "install_tool", {"key": "a"})  # a whole install happens mid-read
            return "stale answer"
        seen.append("fresh")
        return "fresh answer"

    d = dispatcher(install=True, forget=f, runner=Runner({"list_tools": read}))
    before = f.n
    out = call(d, "list_tools")["result"]
    assert out["content"][0]["text"] == "fresh answer"
    assert len(out["content"]) == 1  # no note: the retry was clean
    assert seen == ["stale", "fresh"]
    assert f.n - before >= 3  # burst start, the mutation's finally, and the clear before the retry


def test_a_read_that_overlaps_a_mutation_still_running_is_annotated_not_blocked():
    hold = Latch()
    started = threading.Event()

    def slow_install(t, b):
        started.set()
        hold.release.wait(5)
        return "done"

    def read(t, b):
        m = _thread(lambda: call(d, "install_tool", {"key": "a"}))
        assert started.wait(2)
        read.thread = m
        return "maybe stale"

    d = dispatcher(install=True, runner=Runner({"install_tool": slow_install, "list_tools": read}))
    out = call(d, "list_tools")["result"]
    assert [c["text"] for c in out["content"]] == ["maybe stale", core.INSTALL_NOTE]
    assert out["isError"] is False
    hold.release.set()
    read.thread.join(2)


def test_a_retry_that_straddles_again_is_annotated_rather_than_looped():
    n = [0]

    def read(t, b):
        n[0] += 1
        call(d, "install_tool", {"key": "a"})  # every run is overlapped by a finished install
        return f"run {n[0]}"

    d = dispatcher(install=True, runner=Runner({"list_tools": read}))
    out = call(d, "list_tools")["result"]
    assert n[0] == 2  # exactly one retry
    assert [c["text"] for c in out["content"]] == ["run 2", core.INSTALL_NOTE]


def test_a_failed_read_is_not_retried():
    n = [0]

    def read(t, b):
        n[0] += 1
        call(d, "install_tool", {"key": "a"})
        raise SandboxError("nope")

    d = dispatcher(install=True, runner=Runner({"list_tools": read}))
    assert call(d, "list_tools")["result"]["isError"] is True
    assert n[0] == 1
