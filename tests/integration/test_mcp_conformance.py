"""G-1: the official MCP SDK client against `devstuff mcp`, over stdio and over HTTP (docs/specs/mcp, §6).

Our own tests speak JSON-RPC by hand, so they agree with *our reading* of the protocol. This one speaks
it through the client other people's software uses. The high-level `mcp.Client` opens with
`server/discover`, not `initialize` (F-2), so every connect here exercises the fallback; the lower-level
`ClientSession` never sends it, which is why the main suite uses `Client`. Measured (F-16): neither the
SDK nor Claude Code *fails* on a wrong answer to `server/discover` (`{}`, or none at all — the SDK waits
about five seconds), so what holds those details is the raw-frame section at the bottom.

`mcp` is a dev dependency only; without it the whole module is skipped. Nothing here installs anything:
the gated tool is called with a key that is not in the catalog, so the refusal and the "unknown tool"
result are both reached without an installer ever running. `HOME` is isolated throughout.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("mcp")

import httpx2  # noqa: E402  (installed with mcp)
from mcp import Client, ClientSession, MCPError  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from tests.mcp_process_support import Http, env_for  # noqa: E402

READ_ONLY = {"list_tools", "search_catalog", "tool_info", "outdated", "profile_diff"}
LIMIT = 120


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config" / "devstuff").mkdir(parents=True)
    return h


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, LIMIT))


def stdio_params(home, flags):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "dev_setup", "mcp", *flags], env=env_for(home), cwd=home,
    )


@asynccontextmanager
async def over_stdio(home, flags):
    async with Client(stdio_params(home, flags)) as client:
        yield client


@asynccontextmanager
async def over_http(home, flags, *, token=None):
    server = Http(home, *flags)
    assert server.port, server.banner
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token or server.token}"}, timeout=60)
    try:
        url = f"http://127.0.0.1:{server.port}/mcp"
        async with http_client, Client(streamable_http_client(url, http_client=http_client)) as client:
            yield client
    finally:
        server.kill()


TRANSPORTS = {"stdio": over_stdio, "http": over_http}


@pytest.fixture(params=sorted(TRANSPORTS))
def connect(request, home):
    """`connect(*flags)` -> an initialised SDK session, whichever transport this run is for."""
    def go(*flags, **kw):
        return TRANSPORTS[request.param](home, flags, **kw)
    return go


def expected_names(home, *flags) -> list[str]:
    """What the command line says it exposes — a different route to the same toolbox."""
    out = subprocess.run(
        [sys.executable, "-m", "dev_setup", "mcp", "--list-tools", *flags], env=env_for(home), cwd=home,
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout
    return [line.split()[0] for line in out.splitlines() if line.strip()]


# -- the handshake and the listing -------------------------------------------------------------------


def test_the_sdk_client_initialises_and_lists_exactly_the_read_only_tools(connect, home):
    async def go():
        async with connect() as session:
            return [t.name for t in (await session.list_tools()).tools]

    names = run(go())
    assert set(names) == READ_ONLY
    assert names == expected_names(home)  # same set, same order, as the command line says


def test_every_listed_tool_has_what_a_client_needs_to_offer_it_to_a_model(connect):
    async def go():
        async with connect() as session:
            return (await session.list_tools()).tools

    for tool in run(go()):
        assert tool.description and tool.input_schema["type"] == "object", tool.name
        assert len(tool.name) <= 64 and all(c.isalnum() or c in "_-" for c in tool.name), tool.name


def test_the_server_advertises_the_tools_capability(connect):
    async def go():
        async with connect() as session:
            return session.server_capabilities

    assert run(go()).tools is not None


# -- calls ---------------------------------------------------------------------------------------------


def test_a_read_only_tool_call_returns_text_the_model_can_use(connect):
    async def go():
        async with connect() as session:
            return await session.call_tool("tool_info", {"key": "uv"})

    result = run(go())
    assert not result.is_error
    assert "uv" in result.content[0].text.lower()


def test_search_returns_a_result_for_a_term_in_the_catalog(connect):
    async def go():
        async with connect() as session:
            return await session.call_tool("search_catalog", {"query": "python"})

    result = run(go())
    assert not result.is_error and result.content[0].text.strip()


def test_bad_arguments_are_a_tool_error_the_model_can_read_not_a_protocol_failure(connect):
    async def go():
        async with connect() as session:
            return await session.call_tool("tool_info", {})

    result = run(go())
    assert result.is_error and "key" in result.content[0].text


def test_a_wrong_type_is_a_tool_error_too(connect):
    async def go():
        async with connect() as session:
            return await session.call_tool("outdated", {"packages": "uv"})

    result = run(go())
    assert result.is_error and "packages" in result.content[0].text


# -- gating: absent without the flag, present with it, and refused in between ------------------------------


def test_a_gated_tool_is_absent_and_refused_without_its_flag(connect):
    async def go():
        async with connect() as session:
            names = [t.name for t in (await session.list_tools()).tools]
            try:
                await session.call_tool("install_tool", {"key": "uv"})
            except Exception as exc:  # noqa: BLE001 - the SDK's error type is not the point
                return names, exc
            return names, None

    names, refused = run(go())
    assert "install_tool" not in names
    assert refused is not None, "install_tool was callable without --allow-install"
    assert not any(n.startswith("fn_") for n in names)


def test_install_tool_is_listed_and_callable_with_its_flag_and_nothing_else_changes(connect, home):
    async def go():
        async with connect("--allow-install") as session:
            names = [t.name for t in (await session.list_tools()).tools]
            # A key that is not in the catalog: the call is dispatched, and no installer can run.
            result = await session.call_tool("install_tool", {"key": "no-such-tool-in-the-catalog"})
            return names, result

    names, result = run(go())
    assert set(names) == READ_ONLY | {"install_tool"} and names == expected_names(home, "--allow-install")
    assert result.is_error and "no-such-tool-in-the-catalog" in result.content[0].text


def test_functions_appear_only_with_their_flag(connect, home):
    async def go():
        async with connect("--allow-functions") as session:
            return [t.name for t in (await session.list_tools()).tools]

    names = run(go())
    assert names == expected_names(home, "--allow-functions")
    assert any(n.startswith("fn_") for n in names) and "install_tool" not in names


# -- the failure the HTTP transport adds ---------------------------------------------------------------


def leaves(exc: BaseException):
    """The exceptions inside nested ExceptionGroups: anyio wraps whatever the transport raised."""
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from leaves(inner)
    else:
        yield exc


def test_the_sdk_client_cannot_connect_over_http_with_the_wrong_token(home):
    """The SDK reports a rejected token only as a generic error — the 401 itself is asserted in
    `test_mcp_http.py`. What this shows is that, through the real client, a wrong token is a failure and
    never an empty tool list."""
    async def go():
        async with over_http(home, (), token="not-the-token-not-the-token-1234") as session:
            return await session.list_tools()

    with pytest.raises(BaseException) as err:  # noqa: PT011 - an ExceptionGroup, whatever its members
        run(go())
    assert any(isinstance(e, MCPError) for e in leaves(err.value)), repr(err.value)


# -- the lower-level session: opens with `initialize`, so it needs no fallback ---------------------


@asynccontextmanager
async def session_over_stdio(home, flags):
    async with (
        stdio_client(stdio_params(home, flags)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


@asynccontextmanager
async def session_over_http(home, flags):
    server = Http(home, *flags)
    assert server.port, server.banner
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {server.token}"}, timeout=60)
    try:
        url = f"http://127.0.0.1:{server.port}/mcp"
        async with http_client, streamable_http_client(url, http_client=http_client) as s, ClientSession(
            s[0], s[1]
        ) as session:
            await session.initialize()
            yield session
    finally:
        server.kill()


@pytest.mark.parametrize("opener", [session_over_stdio, session_over_http], ids=["stdio", "http"])
def test_the_lower_level_session_lists_and_calls_too(home, opener):
    async def go():
        async with opener(home, ()) as session:
            tools = [t.name for t in (await session.list_tools()).tools]
            return tools, await session.call_tool("tool_info", {"key": "uv"})

    tools, result = run(go())
    assert set(tools) == READ_ONLY and not result.is_error


# -- raw frames: ids keep their type (FR-16a) and an unknown method is -32601 (FR-16) -----------------------


def raw_stdio(home, frames: list[dict]) -> list[dict]:
    """Send frames to a real `devstuff mcp` and return the replies; EOF on stdin ends the server."""
    proc = subprocess.run(
        [sys.executable, "-m", "dev_setup", "mcp"], env=env_for(home), cwd=home, text=True,
        input="".join(json.dumps(f) + "\n" for f in frames), capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines()]


@pytest.mark.parametrize("rid", [0, "", "server-discover-probe-1", 7, "7"])
def test_a_request_id_comes_back_with_the_same_value_and_type(home, rid):
    (reply,) = raw_stdio(home, [{"jsonrpc": "2.0", "id": rid, "method": "ping"}])
    assert reply["id"] == rid and type(reply["id"]) is type(rid)
    assert "error" not in reply


def test_an_unknown_method_is_method_not_found_so_a_client_can_fall_back(home):
    (reply,) = raw_stdio(home, [{
        "jsonrpc": "2.0", "id": "server-discover-probe-1", "method": "server/discover",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
    }])
    assert reply["error"]["code"] == -32601 and reply["id"] == "server-discover-probe-1"


def test_a_notification_gets_no_reply_and_the_real_clients_sequence_works(home):
    replies = raw_stdio(home, [
        {"jsonrpc": "2.0", "id": "server-discover-probe-1", "method": "server/discover"},
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {"roots": {}, "elicitation": {}},
            "clientInfo": {"name": "claude-code", "version": "2.1.296"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ])
    assert [r["id"] for r in replies] == ["server-discover-probe-1", 0, 1]  # three replies, not four
    assert replies[1]["result"]["protocolVersion"]
    assert {t["name"] for t in replies[2]["result"]["tools"]} == READ_ONLY

