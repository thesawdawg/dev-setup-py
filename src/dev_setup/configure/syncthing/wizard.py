"""`devstuff configure syncthing` — pair this machine with a storage server.

Syncthing is configured through its running REST API, not a file the wizard
writes, which is why `--output` is refused and the apply path takes an explicit
Client (the live test points it at a throwaway daemon rather than the real one).

Two orderings are load-bearing, both measured against syncthing v2.1.6: the
device must exist before the folder that lists it (an unknown device's entry is
silently dropped), and the result must be re-read — a 200 proves nothing (see
model.py's docstring).
"""

from __future__ import annotations

import getpass
import os
import socket
from pathlib import Path

import questionary
from rich.markup import escape
from rich.text import Text

from dev_setup import ui
from dev_setup.configure.syncthing import api, detect, model
from dev_setup.configure.syncthing.model import (
    DEFAULT_FOLDER_PATH,
    DEFAULT_SERVER_NAME,
    FOLDER_MODES,
    Request,
)
from dev_setup.generic import _run


def config_path() -> Path:
    return detect.config_file()


def apply(client: api.Client, config: dict, local_id: str, req: Request, plan: model.Plan) -> None:
    """POST the device (if needed) then the folder — order matters, see above.

    Raises api.ApiError; a failure on the folder write can leave the device
    already added, so what succeeded is attached to the exception as `.wrote`
    for the caller to report.
    """
    req.folder_path.mkdir(parents=True, exist_ok=True)
    wrote: list[str] = []
    try:
        if plan.add_device:
            defaults = client.get("/rest/config/defaults/device")
            client.post("/rest/config/devices", model.build_device(defaults, req))
            wrote.append("the device entry")
        if plan.folder_action == "create":
            defaults = client.get("/rest/config/defaults/folder")
            client.post("/rest/config/folders", model.build_folder(defaults, local_id, req, "create"))
        elif plan.folder_action == "share":
            existing = next(
                f for f in config.get("folders", []) if f.get("id") == req.folder_id
            )
            client.post(
                "/rest/config/folders", model.build_folder(existing, local_id, req, "share")
            )
        if plan.folder_action != "none":
            wrote.append("the folder")
    except api.ApiError as exc:
        exc.wrote = wrote
        raise


def _summary(endpoint: api.Endpoint, local_id: str, req: Request) -> None:
    ui.console.print()
    ui.heading("Done on this side")
    for label, value in [
        ("This machine", f"{socket.gethostname()}"),
        ("Device ID", local_id),
        ("Folder", f"{req.folder_id}  →  {req.folder_path}"),
        ("Mode", FOLDER_MODES.get(req.mode, req.mode)),
        ("Server", f"{req.server_name}  ({req.server_id})"),
        ("Web GUI", endpoint.base_url),
    ]:
        ui.console.print(Text(f"  {label:<12} ") + Text(str(value)))
    ui.console.print()
    ui.console.print(Text("  On the server:", style="bold"))
    for line in (
        f"  1. Add this machine's Device ID  {local_id}  (Actions → Add Remote Device).",
        f"  2. Share folder '{req.folder_id}' with it — it appears under 'Pending' once this side connects.",
    ):
        ui.console.print(Text(line))
    ui.console.print()


def run(*, target: Path | None = None) -> Request | None:
    if target is not None:
        ui.error(
            "--output is not meaningful here: syncthing is configured through its "
            "running local API — there is no file to export."
        )
        return None
    if os.geteuid() == 0:
        ui.error("Run this as your normal user, not root — syncthing is a per-user service.")
        return None

    ui.section("Syncthing pairing")
    ui.dim("Add the storage server as a device and share one folder with it,")
    ui.dim("through syncthing's local API. Nothing is written until you confirm.")
    ui.console.print()

    if not detect.has_systemd() or not detect.user_session():
        ui.error("There is no systemd user session here, so syncthing.service cannot run.")
        ui.dim("  On a server without a login session, the syncthing@<user> system unit is the")
        ui.dim("  alternative; this wizard does not manage it.")
        return None

    if not detect.service_active():
        ui.info("The syncthing user service is not running.")
        if not ui.confirm("Enable and start syncthing.service?", default=True):
            ui.dim("Cancelled — nothing was changed.")
            return None
        try:
            _run(["systemctl", "--user", "enable", "--now", "syncthing.service"])
        except RuntimeError as exc:
            ui.error(f"Could not start the service: {escape(str(exc))}")
            ui.dim("  Look at:  systemctl --user status syncthing.service")
            return None

    endpoint = api.discover(detect.config_file())
    if isinstance(endpoint, str):
        ui.error(escape(endpoint))
        ui.dim("  Look at:  systemctl --user status syncthing.service")
        return None
    with ui.spinner("Waiting for syncthing's API…"):
        status = api.wait_ready(endpoint)
    if status is None:
        ui.error("syncthing's local API did not come up in time.")
        ui.dim("  Look at:  systemctl --user status syncthing.service")
        return None
    client = api.Client(endpoint)

    linger = detect.linger_enabled()
    if linger is False:
        ui.info("Linger is off: syncthing stops when you log out and only starts on login.")
        ui.dim("  Enabling it keeps sync running while you are logged out — it is a")
        ui.dim("  system-wide setting and needs sudo.")
        if ui.confirm("Enable linger for this user? (sudo loginctl enable-linger)", default=True):
            try:
                _run(["sudo", "loginctl", "enable-linger", getpass.getuser()])
            except RuntimeError as exc:
                ui.warn(f"Could not enable linger: {escape(str(exc))}")
            if detect.linger_enabled() is not True:
                ui.warn("Linger still reads as off — sync will pause when you are logged out.")
    # linger None means loginctl could not say — stay quiet rather than warn.

    try:
        config = client.get("/rest/config")
        status = client.get("/rest/system/status")
        local_id = status.get("myID", "")
    except api.ApiError as exc:
        ui.error(f"The local API refused: {escape(str(exc))}")
        ui.dim("  Look at:  systemctl --user status syncthing.service")
        return None
    if not local_id:
        ui.error("syncthing did not report a Device ID — the API is answering but not ready.")
        return None

    # -- the questions ---------------------------------------------------------
    while True:
        raw_id = ui.text_input(
            "Server's Device ID (on the server: Actions → Show ID):", required=True
        )
        problem = model.device_id_problem(raw_id)
        if problem:
            ui.error(f"That ID {escape(problem)}.")
            continue
        server_id = model.normalize_device_id(raw_id)
        if server_id == local_id:
            ui.error("That is this machine's own Device ID — put the server's ID here.")
            continue
        break

    device_known = any(d.get("deviceID") == server_id for d in config.get("devices", []))
    server_name = (
        next(d.get("name", "") for d in config.get("devices", []) if d.get("deviceID") == server_id)
        if device_known
        else ui.text_input("A name for the server:", default=DEFAULT_SERVER_NAME)
    )

    folder_id = ui.text_input(
        "Folder ID to share — it must match the server's exactly:", required=True
    )
    while True:
        raw_path = ui.text_input("Local folder to sync:", default=DEFAULT_FOLDER_PATH)
        folder_path = Path(raw_path).expanduser()
        if not folder_path.is_absolute():
            ui.error("That does not expand to an absolute path — use ~ or a leading /.")
            continue
        folder_path = folder_path.resolve()
        break

    mode = ui.select(
        "Folder mode:",
        [
            questionary.Choice(title=label, value=value)
            for value, label in FOLDER_MODES.items()
        ],
        default="sendreceive",
    )
    if not mode:  # Escape — a silently defaulted mode is a choice nobody made
        ui.dim("Cancelled — nothing was changed.")
        return None

    req = Request(
        server_id=server_id,
        server_name=server_name or DEFAULT_SERVER_NAME,
        folder_id=folder_id,
        folder_path=folder_path,
        mode=mode,
    )

    outcome = model.plan(config, local_id, req)
    if isinstance(outcome, model.Refusal):
        ui.error(escape(outcome.reason))
        return None

    if outcome.add_device or outcome.folder_action != "none":
        ui.console.print()
        ui.heading("This will", note="nothing is written until you confirm")
        for line in outcome.lines:
            ui.console.print(Text(f"  {line}"))
        ui.console.print()
        if not ui.confirm("Apply this to the running syncthing?", default=True):
            ui.dim("Cancelled — nothing was changed.")
            return None

        try:
            apply(client, config, local_id, req, outcome)
        except api.ApiError as exc:
            ui.error(f"The API write failed: {escape(str(exc))}")
            wrote = getattr(exc, "wrote", [])
            ui.dim(
                f"  Written before the failure: {', '.join(wrote)}."
                if wrote
                else "  Nothing was written."
            )
            return None
    else:
        ui.info("This folder is already shared with that device — nothing to change.")

    try:
        config = client.get("/rest/config")
    except api.ApiError as exc:
        ui.error(f"Could not re-read the config to verify it: {escape(str(exc))}")
        return None
    problems = model.verify(config, req)
    if problems:
        for problem in problems:
            ui.error(escape(problem))
        ui.dim("  Check the state in the web GUI — the API returned 200 but the")
        ui.dim("  stored config does not match what was asked for.")
        return None
    ui.success("Verified: the device and folder are in syncthing's config.")
    _summary(endpoint, local_id, req)

    try:
        restart = client.get("/rest/config/restart-required")
    except api.ApiError:
        restart = {}
    if (
        isinstance(restart, dict)
        and restart.get("requiresRestart")
        and ui.confirm("syncthing says a restart is required — restart the service?", default=True)
    ):
        try:
            _run(["systemctl", "--user", "restart", "syncthing.service"])
        except RuntimeError as exc:
            ui.error(f"Restart failed: {escape(str(exc))}")

    return req
