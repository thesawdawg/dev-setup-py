"""A real `devstuff mcp` in a subprocess with an isolated `HOME`, for the command-level tests.

Not a test module (no `test_` prefix). Nothing started through here can see the user's own token file,
catalogs or Claude configuration: `HOME` and `XDG_STATE_HOME` both point inside the test's tmp dir.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def env_for(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["HOME"] = str(home)
    env["XDG_STATE_HOME"] = str(home / "state")
    return env


def token_path(home: Path) -> Path:
    return home / "state" / "devstuff" / "mcp-token"


class Http:
    """A running `devstuff mcp --http`, once its banner has said where it is."""

    def __init__(self, home: Path, *flags: str, new_session: bool = False):
        self.home = home
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dev_setup", "mcp", "--http", "--port", "0", *flags], env=env_for(home),
            cwd=home, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=new_session, text=True,
        )
        self.banner = self.proc.stderr.readline()
        self.port = None
        if m := re.search(r":(\d+) \(", self.banner):
            self.port = int(m.group(1))

    @property
    def token(self) -> str:
        return token_path(self.home).read_text().strip()

    def request(self, method, path, *, body=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {"Host": f"127.0.0.1:{self.port}"}
            if token:
                headers["Authorization"] = f"Bearer {self.token}"
            conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, (json.loads(data) if data else None)
        finally:
            conn.close()

    def stop(self, sig=signal.SIGTERM, timeout=20):
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
        rc = self.proc.wait(timeout)
        self.out = self.proc.stdout.read()
        self.err = self.banner + self.proc.stderr.read()
        return rc

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
