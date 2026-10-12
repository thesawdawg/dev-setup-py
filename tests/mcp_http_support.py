"""A real `devstuff mcp --http` server on an ephemeral port, in-process, for the HTTP tests.

Not a test module (no `test_` prefix). The dispatcher is built by the caller with a fake runner, so
a request can exercise every tool without running an installer.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from dev_setup.agent.registry import AgentParam, AgentTool
from dev_setup.mcp import http as mcp_http
from dev_setup.mcp.core import Dispatcher, Toolbox

TOKEN = "test-token-0123456789abcdef0123456789"


def tool(key, *, impl="catalog", target=None, params=(), mutating=False, description=None):
    return AgentTool(
        key=key, name=key.replace("_", " ").title(), description=description or f"the {key} tool",
        impl=impl, target=target or key, mutating=mutating, params=list(params),
    )


KEY = AgentParam("key", "string", "a catalog key", required=True)
FLAG = AgentParam("installed_only", "boolean", "only installed ones", required=False, default=False)
LIST = AgentParam("packages", "array", "keys to check", required=False)

# Enough variety to exercise every branch of the document: a tool with no parameters, one with an
# optional parameter, one with a required one, an array parameter, and both gated classes.
TOOLS = {
    "list_tools": tool("list_tools", params=[FLAG]),
    "tool_info": tool("tool_info", params=[KEY]),
    "outdated": tool("outdated", params=[LIST]),
    "search_catalog": tool("search_catalog"),
    "install_tool": tool("install_tool", params=[KEY]),
    "fn_x": tool("fn_x", impl="function", target="x", mutating=True, params=[KEY]),
}


def toolbox(*, install=False, functions=False, tools=None, warn=None) -> Toolbox:
    return Toolbox.from_agent(
        TOOLS if tools is None else tools, allow_install=install, allow_functions=functions,
        warn=warn or (lambda m: None),
    )


@dataclass
class Calls:
    seen: list[tuple[str, dict]] = field(default_factory=list)

    def __call__(self, t, bound):
        self.seen.append((t.key, bound))
        return f"ran {t.key}"


@dataclass
class Running:
    port: int
    token: str
    calls: Calls
    server: object

    def request(self, method, path, *, body=None, headers=None, token=True, host="default", raw=None):
        """One request, with full control of the headers a browser or a rebinding page would send."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            hdrs = dict(headers or {})
            if token is True:
                hdrs.setdefault("Authorization", f"Bearer {self.token}")
            elif token:
                hdrs.setdefault("Authorization", f"Bearer {token}")
            if host == "default":
                hdrs.setdefault("Host", f"127.0.0.1:{self.port}")
            elif host is not None:
                hdrs["Host"] = host
            payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
            if payload is not None:
                hdrs.setdefault("Content-Type", "application/json")
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in hdrs.items():
                conn.putheader(k, v)
            if payload is not None:
                conn.putheader("Content-Length", str(len(payload)))
            conn.endheaders(payload)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        finally:
            conn.close()

    def json(self, method, path, **kw):
        status, headers, data = self.request(method, path, **kw)
        return status, headers, (json.loads(data) if data else None)


@contextmanager
def running(*, install=False, functions=False, allow_hosts=(), allow_origins=(), runner=None,
            tools=None, token=TOKEN, forget=None) -> Iterator[Running]:
    calls = runner or Calls()
    dispatcher = Dispatcher(
        toolbox(install=install, functions=functions, tools=tools), run=calls,
        forget=forget or (lambda: None), log_error=lambda m: None,
    )
    server = mcp_http.make_server(
        dispatcher, host="127.0.0.1", port=0, token=token,
        allow_hosts=allow_hosts, allow_origins=allow_origins,
    )
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield Running(server.server_address[1], token, calls, server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
