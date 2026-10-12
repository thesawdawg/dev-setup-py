"""The HTTP front end: MCP over `POST /mcp`, REST, and the OpenAPI document (docs/specs/mcp).

Every route sits behind the same three checks, in this order, **before** any route is looked at:

1. `Origin` and `Host` (FR-26) — a web page can send requests to `http://localhost:8765`, and DNS
   rebinding makes the browser's own origin checks meaningless, so a request from a browser or a
   rebinding page is refused here. Failure is `403` and is checked *before* the token, so the answer
   never reveals whether a token would have been accepted.
2. The bearer token (FR-24), compared in constant time. `401`.
3. Only then the route, the method and the body.

There are no CORS headers, ever (FR-28): a browser is not a supported client. There are no sessions
and no SSE stream (SD-6): `GET /mcp` is `405`, which Claude Code reads as "this server has no
stream" and carries on (F-8).
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import signal
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from dev_setup import verbose
from dev_setup.mcp import openapi
from dev_setup.mcp.core import (
    Dispatcher,
    UnknownTool,
    rest_result,
)

MAX_BODY = 1024 * 1024  # FR-18: a request body over 1 MiB is refused
_DRAIN_LIMIT = 16 * MAX_BODY  # how much of a refused body is read and thrown away before closing


def host_allowed(header: str | None, *, port: int, allow_hosts: frozenset[str]) -> bool:
    """Whether a `Host` header names this server (FR-26).

    The port must be the bound one, and the name must be `localhost`, an IP-address literal, or one
    given with `--allow-host`. Any other hostname is refused: DNS rebinding needs a *name* the
    attacker controls, which an address literal is not — and a container reaches the host as
    `host.docker.internal` or a bridge IP, so the rule must not hard-code the bound address.
    """
    if not header or any(ch in header for ch in "@/\\ ?#\t"):
        return False  # userinfo, a path or whitespace: not a host and port, whatever it parses as
    try:
        parts = urlsplit("//" + header)
        name, host_port = parts.hostname, parts.port
    except ValueError:
        return False
    if name is None or host_port != port:
        return False
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name in allow_hosts


class _IdleAware:
    """The request stream, reporting whether its connection is waiting for a request.

    The handler reads one line at a time and the connection only *waits* inside that read. A
    keep-alive connection a client has left open is blocked there; a connection running a tool is
    not, because its request line has already been read. Shutdown needs the difference: it must wait
    for the second kind and not for the first (FR-30).
    """

    def __init__(self, raw: Any, server: McpHTTPServer, conn: socket.socket) -> None:
        self._raw, self._server, self._conn = raw, server, conn

    def readline(self, *args: Any) -> bytes:
        self._server.track(self._conn, idle=True)
        try:
            return self._raw.readline(*args)
        finally:
            self._server.track(self._conn, idle=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class McpHTTPServer(ThreadingHTTPServer):
    """A threaded server that carries what its handlers need.

    Request threads are **not** daemons: `server_close()` joins them, so closing the server waits for
    a running install rather than killing it half-way (FR-30).
    """

    daemon_threads = False
    allow_reuse_address = True  # a restart is not blocked by TIME_WAIT; a *busy* port still fails
    request_queue_size = 32

    def __init__(
        self,
        address: tuple[str, int],
        dispatcher: Dispatcher,
        *,
        token: str,
        allow_hosts: frozenset[str],
        allow_origins: frozenset[str],
    ) -> None:
        family = socket.getaddrinfo(address[0], address[1], type=socket.SOCK_STREAM)[0][0]
        self.address_family = family
        self.dispatcher = dispatcher
        self.token = token.encode("utf-8")
        self.allow_hosts = allow_hosts
        self.allow_origins = allow_origins
        self.stopping = False
        self._connections: dict[socket.socket, bool] = {}  # connection -> waiting for a request?
        self._connections_lock = threading.Lock()
        super().__init__(address, _Handler)

    def track(self, conn: socket.socket, *, idle: bool) -> None:
        with self._connections_lock:
            self._connections[conn] = idle

    def untrack(self, conn: socket.socket) -> None:
        with self._connections_lock:
            self._connections.pop(conn, None)

    def server_close(self) -> None:
        """Stop for good, waiting for requests that are *running* — not for idle connections.

        `ThreadingMixIn.server_close` joins every request thread, and a thread whose client left a
        keep-alive connection open is blocked reading a request that will never come. Those are woken
        (their read ends); a busy connection finishes its response and is then told to close.
        """
        self.stopping = True
        with self._connections_lock:
            idle = [c for c, waiting in self._connections.items() if waiting]
        for conn in idle:
            try:
                conn.shutdown(socket.SHUT_RD)
            except OSError:
                pass
        super().server_close()

    def server_bind(self) -> None:
        # HTTPServer.server_bind asks the resolver for this machine's FQDN, which can stall for
        # seconds on a host with a bad resolver; nothing here uses the name.
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = str(self.server_address[0])
        self.server_port = self.server_address[1]

    @property
    def bound_port(self) -> int:
        return int(self.server_address[1])


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: clients reuse a connection for a whole session
    server: McpHTTPServer  # type: ignore[assignment]

    # -- plumbing -----------------------------------------------------------------------------

    def setup(self) -> None:
        super().setup()
        self.rfile = _IdleAware(self.rfile, self.server, self.connection)
        self.server.track(self.connection, idle=True)

    def finish(self) -> None:
        self.server.untrack(self.connection)
        super().finish()

    def version_string(self) -> str:
        return "devstuff"  # not the Python and library versions the default advertises

    def log_message(self, format: str, *args: Any) -> None:
        verbose.log(f"http {self.address_string()} {format % args}")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # The base class calls this for a malformed request line, an over-long header, an unknown
        # protocol version. It would answer in HTML; every body here is JSON.
        self.close_connection = True
        try:
            self._send(code, {"error": message or "bad request"})
        except OSError:
            pass

    def _send_raw_json(self, status: int, text: str) -> None:
        """Send an already-serialised JSON body (a JSON-RPC reply is built as text by the core)."""
        self._send_bytes(status, text.encode("utf-8"), has_body=True, headers=None)

    def _send(self, status: int, payload: Any | None, *, headers: dict[str, str] | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self._send_bytes(status, body, has_body=payload is not None, headers=headers)

    def _send_bytes(
        self, status: int, body: bytes, *, has_body: bool, headers: dict[str, str] | None
    ) -> None:
        self._settle_request_body()
        if self.server.stopping:
            self.close_connection = True
        self.send_response(status)
        if has_body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        verbose.log(f"http {self.command} {self.path} -> {status}")

    def _error(self, status: int, message: str, **kw: Any) -> None:
        self._send(status, {"error": message}, **kw)

    # -- the request body ---------------------------------------------------------------------

    def _content_length(self) -> int | None:
        """The declared body length, or None when it cannot be trusted (sets `close_connection`)."""
        if "Transfer-Encoding" in self.headers:
            return None  # chunked bodies are not decoded here; reading one as the next request is worse
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        if not raw.isascii() or not raw.isdigit():
            return None
        return int(raw)

    def _settle_request_body(self) -> None:
        """Before answering, consume (or abandon the connection over) a body nobody read, so its
        bytes are not parsed as the next request — and so a client sending a large refused body
        still receives the answer instead of a connection reset."""
        if getattr(self, "_body_settled", False):
            return
        self._body_settled = True
        length = self._content_length()
        if length is None:
            self.close_connection = True
            return
        if length == 0:
            return
        if length > _DRAIN_LIMIT:
            self.close_connection = True
            return
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    self.close_connection = True
                    return
                remaining -= len(chunk)
        except OSError:
            self.close_connection = True
            return
        if length > MAX_BODY:
            self.close_connection = True  # we did read it, but a client that sends this much can reconnect

    def _read_body(self) -> bytes | None:
        """The body, or None after having answered `400`/`413` already."""
        length = self._content_length()
        if length is None:
            self._error(400, "invalid Content-Length, or a chunked body (not supported)")
            return None
        if length > MAX_BODY:
            self._error(413, f"request body over {MAX_BODY} bytes")
            return None
        data = self.rfile.read(length) if length else b""
        self._body_settled = True
        if len(data) != length:
            self.close_connection = True
            self._error(400, "request body shorter than its Content-Length")
            return None
        return data

    # -- the checks in front of every route -----------------------------------------------------

    def _origin_ok(self) -> bool:
        origins = self.headers.get_all("Origin") or []
        return all(o in self.server.allow_origins for o in origins)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, credential = header.partition(" ")
        presented = credential.strip().encode("utf-8", "replace") if scheme.lower() == "bearer" else b""
        # Compared even when empty, so a missing credential takes the same path as a wrong one.
        return hmac.compare_digest(presented, self.server.token) and bool(presented)

    # -- routing --------------------------------------------------------------------------------

    def _handle(self) -> None:
        self._body_settled = False
        srv = self.server
        if not self._origin_ok() or not host_allowed(
            self.headers.get("Host"), port=srv.bound_port, allow_hosts=srv.allow_hosts
        ):
            return self._error(403, "forbidden host or origin")
        if not self._authorized():
            return self._error(401, "missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

        path = urlsplit(self.path).path
        method = self.command
        if path == "/mcp":
            return self._mcp() if method == "POST" else self._not_allowed("POST")
        if path == "/tools":
            return self._tool_list() if method == "GET" else self._not_allowed("GET")
        if path == "/openapi.json":
            return self._document() if method == "GET" else self._not_allowed("GET")
        name = path.removeprefix("/tools/") if path.startswith("/tools/") else ""
        if name and "/" not in name:
            # A method check first, for every name: GET on a gated-off tool must look exactly like
            # GET on one that never existed (FR-3).
            return self._rest_call(name) if method == "POST" else self._not_allowed("POST")
        return self._error(404, "not found")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    def _not_allowed(self, allow: str) -> None:
        self._error(405, f"method not allowed; use {allow}", headers={"Allow": allow})

    # -- the routes -----------------------------------------------------------------------------

    def _mcp(self) -> None:
        raw = self._read_body()
        if raw is None:
            return
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            from dev_setup.mcp.core import error_text

            reply: str | None = error_text(None, -32700, "parse error: not valid UTF-8")
        else:
            reply = self.server.dispatcher.handle_text(text)
        if reply is None:
            return self._send(202, None)  # a notification: accepted, nothing to say
        self._send_raw_json(200, reply)

    def _tool_list(self) -> None:
        self._send(200, self.server.dispatcher.toolbox.openai_tools())

    def _document(self) -> None:
        # `Host` was validated above, so it is a name this client can reach us by — a container gets
        # `host.docker.internal`, not `127.0.0.1`, in the URL it will follow.
        url = f"http://{self.headers.get('Host')}"
        self._send(200, openapi.build(self.server.dispatcher.toolbox, server_url=url))

    def _rest_call(self, name: str) -> None:
        dispatcher = self.server.dispatcher
        if dispatcher.toolbox.get(name) is None:
            return self._error(404, "not found")  # unknown and gated-off read the same (FR-3)
        raw = self._read_body()
        if raw is None:
            return
        if not raw.strip():
            if dispatcher.toolbox.requires_body(name):
                return self._error(400, "this tool has a required parameter: send a JSON object body")
            arguments: dict[str, Any] = {}
        else:
            try:
                arguments = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                return self._error(400, "the body is not valid JSON")
            if not isinstance(arguments, dict):
                return self._error(400, "the body must be a JSON object")
        try:
            result = dispatcher.call(name, arguments)
        except UnknownTool:
            return self._error(404, "not found")
        self._send(200, rest_result(result))


def make_server(
    dispatcher: Dispatcher,
    *,
    host: str,
    port: int,
    token: str,
    allow_hosts: tuple[str, ...] | frozenset[str] = (),
    allow_origins: tuple[str, ...] | frozenset[str] = (),
) -> McpHTTPServer:
    """Bind and return the server. Raises `OSError` when the address is busy or unusable."""
    return McpHTTPServer(
        (host, port),
        dispatcher,
        token=token,
        allow_hosts=frozenset(h.lower() for h in allow_hosts),
        allow_origins=frozenset(allow_origins),
    )


def serve(server: McpHTTPServer) -> int:
    """Serve until SIGINT/SIGTERM, then wait for running calls and return 0 (FR-30).

    The first signal stops accepting requests; a request already running is allowed to finish,
    because an installer killed half-way is worse than one waited for. A second signal exits at once.
    """
    received = 0

    def on_signal(signum: int, _frame: object) -> None:
        nonlocal received
        received += 1
        if received > 1:
            os._exit(128 + signum)
        # `shutdown()` waits for `serve_forever` to return, and that loop is running on this very
        # thread — calling it from here would wait for itself.
        threading.Thread(target=server.shutdown, name="mcp-shutdown", daemon=True).start()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        # The default poll interval is 0.5s, which is how long a shutdown would wait to notice.
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()  # joins the request threads: a running install finishes first
    return 0
