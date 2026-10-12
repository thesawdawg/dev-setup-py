"""Talking to syncthing's local REST API.

No new dependencies — stdlib `urllib` against the address and API key found in
`syncthing paths`' config.xml. The API key only ever travels in the `X-API-Key`
header; it must never appear in output, errors or a process command line.
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    api_key: str
    verify_tls: bool

    def __repr__(self) -> str:  # never leak the key into logs or tracebacks
        return f"Endpoint(base_url={self.base_url!r}, api_key=<redacted>, verify_tls={self.verify_tls!r})"


def discover(config_file: Path) -> Endpoint | str:
    """The Endpoint config.xml describes, or a sentence saying why it is unusable."""
    try:
        root = ET.parse(config_file).getroot()
    except OSError:
        return f"no config file at {config_file} — has `syncthing` been run once?"
    except ET.ParseError as exc:
        return f"{config_file} does not parse as XML: {exc}"

    gui = root.find("gui")
    if gui is None:
        return f"{config_file} has no <gui> element"
    address = (gui.findtext("address") or "").strip()
    api_key = (gui.findtext("apikey") or "").strip()
    if not api_key:
        return f"{config_file} has no <apikey> under <gui>"
    if address.startswith("unix://"):
        return (
            f"the GUI listens on a unix socket ({address}); this wizard only speaks http(s) — "
            "change the listen address in the Syncthing GUI"
        )

    if ":" not in address:
        return f"could not parse the GUI address '{address}'"
    host, _, port = address.rpartition(":")
    host = host.strip("[]")

    tls = gui.get("tls", "false").lower() == "true"
    wildcard = {"0.0.0.0", "", "::"}
    if host in wildcard:
        host = "127.0.0.1" if host in ("0.0.0.0", "") else "::1"
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() in ("localhost", "localhost.localdomain")
    if not loopback:
        return (
            f"the GUI is bound to {host}:{port}, which is not a loopback address — "
            f"refusing to send the API key there; set the GUI listen address to "
            f"127.0.0.1:{port} in the Syncthing GUI (Actions → Settings → GUI) and re-run"
        )

    scheme = "https" if tls else "http"
    if ":" in host:  # IPv6 literal needs brackets in a URL
        host = f"[{host}]"
    return Endpoint(base_url=f"{scheme}://{host}:{port}", api_key=api_key, verify_tls=not tls)


class ApiError(Exception):
    """One error type for every API failure. Carries status and body, never the key."""


class Client:
    def __init__(self, endpoint: Endpoint, timeout: float = 5.0):
        self._endpoint = endpoint
        self._timeout = timeout
        handlers: list = [
            # A corporate HTTP proxy must never see the local daemon's API key.
            urllib.request.ProxyHandler({}),
        ]
        if not endpoint.verify_tls:
            # tls="true" on a loopback GUI serves a self-signed cert; the key is
            # sent only to 127.0.0.1/[::1], so skipping verification is safe here.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    def _request(self, method: str, path: str, data: object = None) -> object:
        url = self._endpoint.base_url + path
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("X-API-Key", self._endpoint.api_key)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode(errors="replace").strip()
            except Exception:
                detail = ""
            raise ApiError(f"{method} {path}: HTTP {exc.code} {exc.reason} {detail}".strip()) from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ApiError(f"{method} {path}: {reason}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"{method} {path}: response was not JSON") from exc

    def get(self, path: str) -> object:
        return self._request("GET", path)

    def post(self, path: str, data: object) -> object:
        return self._request("POST", path, data)


def wait_ready(endpoint: Endpoint, timeout: float = 30.0) -> dict | None:
    """Poll `system/status` until the daemon answers, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = Client(endpoint).get("/rest/system/status")
            if isinstance(status, dict) and status.get("myID"):
                return status
        except ApiError:
            pass
        time.sleep(0.5)
    return None
