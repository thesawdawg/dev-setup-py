"""The HTTP front end: MCP over `POST /mcp`, REST, and the checks that stand in front of both
(docs/specs/mcp, FR-20, FR-21, FR-23, FR-24–28, SD-6, SD-7, SD-8).

These run a real `ThreadingHTTPServer` on an ephemeral port and talk to it with `http.client`, because
the security rules are about what a *client* can make a server do — a header a browser sends, a
`Host` a DNS-rebinding page forces — not about what a handler function returns.
"""

from __future__ import annotations

import hmac
import http.client
import json
import socket
import threading

import pytest

from dev_setup.agent.sandbox import SandboxError
from dev_setup.mcp import http as mcp_http
from tests.mcp_http_support import TOKEN, Calls, running

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
ROUTES = [("POST", "/mcp"), ("GET", "/tools"), ("GET", "/openapi.json"), ("POST", "/tools/list_tools")]


# -- FR-20: MCP over POST /mcp ----------------------------------------------------------------------


def test_a_request_gets_a_200_json_reply_with_its_id_echoed():
    with running() as srv:
        status, headers, body = srv.json("POST", "/mcp", body=INIT,
                                         headers={"Accept": "application/json, text/event-stream"})
        assert status == 200 and headers["content-type"].startswith("application/json")
        assert body["id"] == 0 and body["result"]["protocolVersion"] == "2025-11-25"
        assert "mcp-session-id" not in headers  # SD-6: stateless, never issued


def test_a_notification_is_202_with_no_body():
    with running() as srv:
        status, headers, data = srv.request(
            "POST", "/mcp", body={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert status == 202 and data == b"" and headers["content-length"] == "0"


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_get_and_delete_on_mcp_are_405_so_a_client_knows_there_is_no_stream(method):
    """F-8: Claude Code sends `GET /mcp` for an SSE stream and takes 405 as 'none here'."""
    with running() as srv:
        status, headers, body = srv.json(method, "/mcp", headers={"Accept": "text/event-stream"})
        assert status == 405 and headers["allow"] == "POST" and "error" in body


def test_json_rpc_level_errors_are_a_200_with_the_error_in_the_body():
    with running() as srv:
        assert srv.json("POST", "/mcp", raw=b"{nope")[2]["error"]["code"] == -32700
        assert srv.json("POST", "/mcp", body=[INIT])[2]["error"]["code"] == -32600
        assert srv.json("POST", "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "server/discover"})[2][
            "error"]["code"] == -32601


def test_a_gated_tool_is_refused_over_mcp_and_never_run():
    with running() as srv:
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "install_tool", "arguments": {"key": "uv"}}}
        assert srv.json("POST", "/mcp", body=call)[2]["error"]["code"] == -32602
        assert srv.calls.seen == []
    with running(install=True) as srv:
        assert srv.json("POST", "/mcp", body=call)[2]["result"]["isError"] is False
        assert srv.calls.seen == [("install_tool", {"key": "uv"})]


# -- FR-24, FR-26: the checks in front of every route ------------------------------------------------


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_wants_the_token(method, path):
    with running() as srv:
        for token in (False, "wrong-token-0123456789abcdef0123456789"):
            status, headers, body = srv.json(method, path, token=token, body={} if method == "POST" else None)
            assert status == 401 and headers["www-authenticate"] == "Bearer" and "error" in body
        assert srv.calls.seen == []


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_checks_the_host_before_the_token(method, path):
    """FR-26: a 403 for a bad `Host` is the same whether or not the token is right, so the response
    never reveals whether a token would have been accepted."""
    with running() as srv:
        for token in (True, False, "wrong"):
            status, _, _ = srv.request(method, path, host="evil.example", token=token)
            assert status == 403
        assert srv.calls.seen == []


def test_a_good_host_with_no_token_is_401_not_403():
    with running() as srv:
        assert srv.request("GET", "/tools", token=False)[0] == 401


def test_the_scheme_is_case_insensitive_but_the_token_is_not():
    with running() as srv:
        def status(authorization):
            return srv.request("GET", "/tools", token=False, headers={"Authorization": authorization})[0]

        assert status(f"bearer {TOKEN}") == 200
        assert status(f"Bearer {TOKEN.upper()}") == 401
        assert status(f"Basic {TOKEN}") == 401
        assert status(TOKEN) == 401
        assert status("Bearer") == 401


def test_the_token_is_compared_with_compare_digest(monkeypatch):
    """Constant time is not observable from outside a test, so check the right primitive is used,
    and that a mismatch in length does not raise."""
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(mcp_http.hmac, "compare_digest", spy)
    with running() as srv:
        assert srv.request("GET", "/tools")[0] == 200
        assert srv.request("GET", "/tools", token="x")[0] == 401
    assert len(calls) >= 2 and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)


@pytest.mark.parametrize(("host", "ok"), [
    ("{port_host}", True),
    ("localhost:{port}", True),
    ("LOCALHOST:{port}", True),
    ("127.0.0.1:{port}", True),
    ("[::1]:{port}", True),
    ("192.168.1.20:{port}", True),  # an address literal is not a name an attacker controls
    ("localhost", False),  # no port: not the bound port
    ("localhost:1", False),
    ("evil.example:{port}", False),
    ("evil.example", False),
    ("localhost.:{port}", False),
    ("localhost:{port}@evil.example", False),  # userinfo trick: the host is evil.example
    ("evil.example:{port}/localhost", False),
    ("", False),
    ("localhost:{port} ", False),
    ("host.docker.internal:{port}", False),  # only with --allow-host
])
def test_the_host_rule(host, ok):
    with running() as srv:
        host = host.format(port=srv.port, port_host=f"127.0.0.1:{srv.port}")
        assert (srv.request("GET", "/tools", host=host)[0] == 200) is ok


def test_a_missing_host_header_is_refused():
    with running() as srv:
        # HTTP/1.0 allows it, and a proxy or a script might send it: no Host is not a loopback Host.
        with socket.create_connection(("127.0.0.1", srv.port), timeout=10) as conn:
            conn.sendall(f"GET /tools HTTP/1.0\r\nAuthorization: Bearer {TOKEN}\r\n\r\n".encode())
            reply = b""
            while chunk := conn.recv(4096):
                reply += chunk
        assert reply.split(b"\r\n", 1)[0].split()[1] == b"403"
        assert srv.request("GET", "/tools", host=None)[0] == 403


def test_allow_host_admits_a_named_host_on_the_bound_port_only():
    with running(allow_hosts=("host.docker.internal",)) as srv:
        assert srv.request("GET", "/tools", host=f"host.docker.internal:{srv.port}")[0] == 200
        assert srv.request("GET", "/tools", host="host.docker.internal:1")[0] == 403
        assert srv.request("GET", "/tools", host=f"other.internal:{srv.port}")[0] == 403


def test_an_origin_header_is_refused_unless_listed():
    with running(allow_origins=("http://localhost:3000",)) as srv:
        assert srv.request("GET", "/tools")[0] == 200  # no Origin: not a browser
        assert srv.request("GET", "/tools", headers={"Origin": "http://evil.example"})[0] == 403
        assert srv.request("GET", "/tools", headers={"Origin": "null"})[0] == 403
        assert srv.request("GET", "/tools", headers={"Origin": "http://localhost:3000"})[0] == 200
        assert srv.request("GET", "/tools", headers={"Origin": "http://localhost:3000/"})[0] == 403  # exact


def test_an_unlisted_origin_is_refused_before_the_token_is_looked_at():
    with running() as srv:
        for token in (True, False):
            status = srv.request("POST", "/mcp", body=INIT, token=token, headers={"Origin": "http://x"})[0]
            assert status == 403


def test_no_cors_header_is_ever_sent():
    with running(allow_origins=("http://localhost:3000",)) as srv:
        responses = [
            srv.request("GET", "/tools", headers={"Origin": "http://localhost:3000"}),
            srv.request("OPTIONS", "/tools", headers={"Origin": "http://localhost:3000",
                                                       "Access-Control-Request-Method": "POST"}),
            srv.request("GET", "/tools", token=False),
            srv.request("GET", "/tools", host="evil.example"),
            srv.request("GET", "/nope"),
        ]
        for status, headers, _ in responses:
            assert not [h for h in headers if h.startswith("access-control-")], status
        assert responses[1][0] == 405  # a preflight is just a method nobody supports


def test_the_token_never_appears_in_any_response():
    with running() as srv:
        everything = [
            srv.request("GET", "/tools"), srv.request("GET", "/openapi.json"),
            srv.request("POST", "/mcp", body=INIT), srv.request("GET", "/tools", token=False),
            srv.request("GET", "/tools", token="wrong"), srv.request("GET", "/tools", host="evil.example"),
            srv.request("GET", "/nope"), srv.request("PUT", "/tools"),
        ]
        for _, headers, data in everything:
            assert TOKEN.encode() not in data
            assert TOKEN not in json.dumps(headers)


# -- FR-20/22: limits, framing and the connection ------------------------------------------------------


def test_the_server_does_not_say_what_it_is_running_on():
    with running() as srv:
        server = srv.request("GET", "/tools")[1].get("server", "")
        assert "python" not in server.lower() and "basehttp" not in server.lower()


@pytest.mark.parametrize("path", ["/mcp", "/tools/list_tools"])
def test_a_body_over_one_mebibyte_is_413_and_the_client_still_gets_the_answer(path):
    with running() as srv:
        status, _, body = srv.json("POST", path, raw=b" " * (mcp_http.MAX_BODY + 1))
        assert status == 413 and "error" in body
        assert srv.calls.seen == []
        assert srv.request("GET", "/tools")[0] == 200  # and the server is fine


def test_a_large_refused_body_still_gets_its_answer_instead_of_a_reset():
    """Measured: a server that answers and closes with a big body unread resets the connection, and
    a client sending 8 MB then sees BrokenPipe, never the 413. The body is read and dropped first."""
    with running() as srv:
        status, _, body = srv.json("POST", "/tools/list_tools", raw=b" " * (8 * 1024 * 1024))
        assert status == 413 and "error" in body


def test_a_body_of_exactly_the_limit_is_accepted():
    with running() as srv:
        assert srv.request("POST", "/mcp", raw=b" " * mcp_http.MAX_BODY)[0] == 200  # a -32700, but accepted


def test_an_unauthenticated_oversized_body_is_401_not_413():
    with running() as srv:
        assert srv.request("POST", "/mcp", raw=b" " * (mcp_http.MAX_BODY + 1), token=False)[0] == 401


@pytest.mark.parametrize("header", ["abc", "-1", "1e3"])
def test_a_bad_content_length_is_400(header):
    with running() as srv:
        assert srv.request("POST", "/tools/list_tools", headers={"Content-Length": header})[0] == 400


def test_a_chunked_request_body_is_refused_rather_than_misread():
    with running() as srv:
        status, _, _ = srv.request(
            "POST", "/tools/list_tools", headers={"Transfer-Encoding": "chunked"}, raw=b"0\r\n\r\n"
        )
        assert status == 400


def test_a_connection_can_be_reused_after_an_error_that_left_a_body_unread():
    """HTTP/1.1 keep-alive: a rejected request's body must be consumed (or the connection closed),
    or its bytes would be read as the next request."""
    with running() as srv:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        try:
            attempts = (
                ("/tools/list_tools", "wrong", 401), ("/nope", TOKEN, 404), ("/tools/list_tools", TOKEN, 200),
            )
            for path, token, want in attempts:
                headers = {"Authorization": f"Bearer {token}", "Host": f"127.0.0.1:{srv.port}"}
                conn.request("POST", path, body=b'{"a": 1}', headers=headers)
                resp = conn.getresponse()
                resp.read()
                # Every answer, not just the last: if the unread body were parsed as the next request
                # line, the server would answer 400 (and the client would quietly reconnect).
                assert resp.status == want, path
                assert not resp.will_close, path
        finally:
            conn.close()


def test_unknown_paths_and_methods_are_json_errors_not_html():
    with running() as srv:
        for method, path, want in [("GET", "/nope", 404), ("GET", "/", 404), ("GET", "/tools/", 404),
                                   ("POST", "/tools/a/b", 404), ("PUT", "/tools", 405),
                                   ("PATCH", "/mcp", 405), ("HEAD", "/tools", 405), ("OPTIONS", "/mcp", 405)]:
            status, headers, data = srv.request(method, path)
            assert status == want, (method, path)
            if method != "HEAD":
                assert "error" in json.loads(data) and headers["content-type"].startswith("application/json")
            else:
                assert data == b""


def test_a_query_string_does_not_change_the_route():
    with running() as srv:
        assert srv.request("GET", "/tools?x=1")[0] == 200
        assert srv.request("POST", "/tools/list_tools?x=1", body={})[0] == 200


# -- FR-21: the OpenAI function list ----------------------------------------------------------------------


def test_get_tools_is_the_openai_function_array_of_exactly_the_exposed_tools():
    with running() as srv:
        status, _, tools = srv.json("GET", "/tools")
        assert status == 200
        names = {t["function"]["name"] for t in tools}
        assert names == {"list_tools", "tool_info", "outdated", "search_catalog"}
        assert all(t["type"] == "function" for t in tools)
        info = next(t for t in tools if t["function"]["name"] == "tool_info")["function"]
        assert info["parameters"] == {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "a catalog key"}},
            "required": ["key"], "additionalProperties": False,
        }
        listed = next(t for t in tools if t["function"]["name"] == "list_tools")["function"]["parameters"]
        assert "required" not in listed  # FR-16c: an empty `required` is omitted


# -- FR-23: POST /tools/<key> ---------------------------------------------------------------------------


def rest(srv, name, **kw):
    return srv.json("POST", f"/tools/{name}", **kw)


def test_a_call_runs_the_tool_and_returns_is_error_and_text():
    with running() as srv:
        status, _, body = rest(srv, "tool_info", body={"key": "uv"})
        assert (status, body) == (200, {"is_error": False, "text": "ran tool_info"})
        assert srv.calls.seen == [("tool_info", {"key": "uv"})]


def test_a_failing_tool_is_a_200_with_is_error_not_an_http_error():
    def boom(t, b):
        raise SandboxError("unknown catalog tool 'zz'")

    class Boom(Calls):
        def __call__(self, t, b):
            boom(t, b)

    with running(runner=Boom()) as srv:
        status, _, body = rest(srv, "tool_info", body={"key": "zz"})
        assert status == 200 and body == {"is_error": True, "text": "unknown catalog tool 'zz'"}


# FR-23's table. `with` = a tool with a required parameter, `without` = one with none.
@pytest.mark.parametrize(("name", "raw", "want", "is_error"), [
    ("tool_info", None, 400, None),            # absent body, required parameter
    ("tool_info", b"", 400, None),
    ("tool_info", b"   ", 400, None),
    ("tool_info", b"{}", 200, True),           # runs far enough to name the missing parameter
    ("tool_info", b'{"key": "uv", "extra": 1}', 200, True),
    ("tool_info", b"null", 400, None),
    ("tool_info", b"[]", 400, None),
    ("tool_info", b'"x"', 400, None),
    ("tool_info", b"3", 400, None),
    ("tool_info", b"{not json", 400, None),
    ("tool_info", b'{"key": "uv"}', 200, False),
    ("search_catalog", None, 200, False),      # absent body, no required parameter: runs as {}
    ("search_catalog", b"", 200, False),
    ("search_catalog", b"{}", 200, False),
    ("search_catalog", b'{"extra": 1}', 200, True),
    ("search_catalog", b"null", 400, None),
    ("search_catalog", b"[]", 400, None),
    ("search_catalog", b'"x"', 400, None),
    ("search_catalog", b"3", 400, None),
    ("search_catalog", b"{not json", 400, None),
    ("tool_info", b"\xff\xfe", 400, None),     # not UTF-8
])
def test_every_row_of_the_body_table(name, raw, want, is_error):
    with running() as srv:
        status, _, body = srv.json("POST", f"/tools/{name}", raw=raw)
        assert status == want, body
        if is_error is not None:
            assert body["is_error"] is is_error
        else:
            assert "error" in body
        if want == 400:
            assert srv.calls.seen == []  # a tool that cannot run without arguments is never run without them


def test_a_curl_with_no_body_works_for_a_tool_that_needs_none():
    with running() as srv:
        status, _, body = srv.json("POST", "/tools/list_tools")
        assert status == 200 and body["is_error"] is False


def test_a_gated_off_tool_is_exactly_as_missing_as_an_unknown_one():
    with running() as srv:
        gated = rest(srv, "install_tool", body={"key": "uv"})
        unknown = rest(srv, "no_such_tool", body={"key": "uv"})
        assert gated[0] == unknown[0] == 404
        same = unknown[2]["error"].replace("no_such_tool", "X")
        assert gated[2]["error"].replace("install_tool", "X") == same
        gated_get = srv.request("GET", "/tools/install_tool")[0]
        assert gated_get == srv.request("GET", "/tools/no_such_tool")[0] == 405
        assert srv.calls.seen == []


def test_the_flag_turns_the_rest_operation_on():
    with running(install=True) as srv:
        assert rest(srv, "install_tool", body={"key": "uv"})[0] == 200
        assert rest(srv, "fn_x", body={"key": "a"})[0] == 404  # the other flag is independent
        assert srv.calls.seen == [("install_tool", {"key": "uv"})]


# -- concurrency and shutdown --------------------------------------------------------------------------


class Slow(Calls):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self, t, bound):
        if t.key == "install_tool":
            self.entered.set()
            self.release.wait(10)
        return super().__call__(t, bound)


def test_a_slow_install_does_not_hold_up_a_read_over_http():
    slow = Slow()
    with running(install=True, runner=slow) as srv:
        result = []
        t = threading.Thread(target=lambda: result.append(rest(srv, "install_tool", body={"key": "a"})))
        t.start()
        assert slow.entered.wait(5)
        assert srv.request("GET", "/tools")[0] == 200  # answered while the install runs
        assert rest(srv, "tool_info", body={"key": "a"})[0] == 200
        slow.release.set()
        t.join(10)
        assert result and result[0][0] == 200


def test_closing_the_server_waits_for_a_running_call_and_the_client_still_gets_its_answer():
    """FR-30: killing an installer half-way is worse than waiting for it."""
    slow = Slow()
    result: list = []
    with running(install=True, runner=slow) as srv:
        t = threading.Thread(target=lambda: result.append(rest(srv, "install_tool", body={"key": "a"})))
        t.start()
        assert slow.entered.wait(5)
        closer = threading.Thread(target=srv.server.shutdown)
        closer.start()
        closer.join(5)  # stops accepting
        done = threading.Event()
        threading.Thread(target=lambda: (srv.server.server_close(), done.set()), daemon=True).start()
        assert not done.wait(0.5)  # server_close() is waiting for the install
        slow.release.set()
        assert done.wait(10)
        t.join(10)
        assert result and result[0][0] == 200 and result[0][2]["is_error"] is False


# -- the pure host rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("header", "ok"), [
    ("localhost:8765", True), ("127.0.0.1:8765", True), ("[::1]:8765", True), ("10.0.0.5:8765", True),
    ("[fe80::1]:8765", True), ("localhost:8766", False), ("localhost", False), (None, False), ("", False),
    ("a@localhost:8765", False), ("localhost:8765/x", False), ("x.example:8765", False),
    ("localhost:abc", False), ("[::1", False),
])
def test_host_allowed(header, ok):
    assert mcp_http.host_allowed(header, port=8765, allow_hosts=frozenset()) is ok


def test_host_allowed_with_a_named_host():
    allow = frozenset({"host.docker.internal"})
    assert mcp_http.host_allowed("Host.Docker.Internal:8765", port=8765, allow_hosts=allow)
    assert not mcp_http.host_allowed("host.docker.internal:80", port=8765, allow_hosts=allow)
