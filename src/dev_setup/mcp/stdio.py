"""The stdio transport: newline-delimited JSON-RPC over a private channel (docs/specs/mcp).

The protocol owns stdout, and devstuff writes to stdout from places nobody can audit for good: the
Rich console, `_run`'s verbose branch, `function_runner`, and every child process (F-1). So the
channel is moved at the *descriptor* level, before any tool can run — private `dup`s of the real
stdin and stdout, then fd 1 pointed at stderr and fd 0 at `/dev/null`. After that there is no path
by which a stray byte can reach the client: stdout has no way to *be* the protocol.

Requests run on their own threads, because a read must not wait behind an install that takes
minutes (FR-8, FR-8a). Replies are written under one lock, one line each.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from dataclasses import dataclass
from typing import BinaryIO

from dev_setup import verbose
from dev_setup.mcp.core import Dispatcher, error_text

MAX_FRAME = 1024 * 1024  # FR-18: a request over 1 MiB is refused


@dataclass
class Channel:
    """The client's end of the conversation, on descriptors nothing else holds."""

    rfile: BinaryIO
    wfile: BinaryIO


def isolate_stdio() -> Channel:
    """Move the protocol onto private descriptors and repoint the standard ones (FR-11).

    The private fds are not inheritable (PEP 446 makes `os.dup` results so), so no child can read
    from or write to the channel; fd 0 and fd 1 stay inheritable, so a child that reads stdin sees
    EOF and one that writes stdout lands on stderr.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    in_fd = os.dup(0)
    out_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    os.dup2(2, 1)
    # Unbuffered out: a reply is written whole and at once, and there is no buffer left holding a
    # frame a dead client never took (which would be flushed, and fail, again at interpreter exit).
    return Channel(os.fdopen(in_fd, "rb"), os.fdopen(out_fd, "wb", buffering=0))


class _Shutdown(Exception):
    """Raised by the first SIGINT/SIGTERM to break the main thread out of a blocking read."""


def serve(
    dispatcher: Dispatcher, rfile: BinaryIO, wfile: BinaryIO, *, max_frame: int = MAX_FRAME
) -> int:
    """Read frames until EOF (or a shutdown), answer each, and return the exit status.

    On the way out it waits for every request still running: an installer killed half-way is worse
    than one waited for (FR-30), and a reply to a call that was already accepted is still owed.
    """
    write_lock = threading.Lock()
    workers: list[threading.Thread] = []

    def reply(text: str) -> None:
        view = memoryview(text.encode("utf-8") + b"\n")
        with write_lock:
            try:
                while view:  # a raw file may take less than all of it
                    written = wfile.write(view)
                    view = view[written:] if written else view
                wfile.flush()
            except (OSError, ValueError):
                # The client has gone. The next read says so (EOF); until then there is nobody to
                # tell, and a write error must never take the loop down.
                pass

    def work(line: bytes) -> None:
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            out: str | None = error_text(None, -32700, "parse error: not valid UTF-8")
        else:
            verbose.log(f"mcp ← {_clip(text)}")
            out = dispatcher.handle_text(text)
        if out is not None:
            verbose.trace(f"mcp → {_clip(out)}")
            reply(out)

    try:
        while True:
            line = rfile.readline(max_frame + 1)
            if not line:
                break  # EOF
            if len(line) > max_frame and not line.endswith(b"\n"):
                _discard_line(rfile)
                reply(error_text(None, -32600, f"request too large (limit {max_frame} bytes)"))
                continue
            if not line.strip():
                continue
            workers = [w for w in workers if w.is_alive()]
            worker = threading.Thread(target=work, args=(line,), name="mcp-request")
            workers.append(worker)
            worker.start()
    except _Shutdown:
        pass

    while True:
        try:
            for worker in workers:
                worker.join()
            break
        except _Shutdown:
            continue  # a first signal that arrived late: still wait; a second one exits at once
    return 0


def _discard_line(rfile: BinaryIO) -> None:
    """Drop the rest of an over-long line, so its tail is not read as the next frame."""
    while True:
        chunk = rfile.readline(64 * 1024)
        if not chunk or chunk.endswith(b"\n"):
            return


def _clip(text: str, limit: int = 300) -> str:
    text = text.rstrip("\n")
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit} chars)"


def run(dispatcher: Dispatcher) -> int:
    """Isolate stdio, install the shutdown signals, and serve until the client goes away.

    The first SIGINT/SIGTERM stops reading new requests and lets the running ones finish; a second
    exits immediately (FR-30).
    """
    channel = isolate_stdio()
    received = 0

    def on_signal(signum: int, _frame: object) -> None:
        nonlocal received
        received += 1
        if received > 1:
            os._exit(128 + signum)
        raise _Shutdown

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    return serve(dispatcher, channel.rfile, channel.wfile)
