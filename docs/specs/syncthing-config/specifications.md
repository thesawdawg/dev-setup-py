# Specification: `devstuff install syncthing` + `devstuff configure syncthing`

**Date:** 2026-10-12
**Status:** Implemented (v1)
**Authors:** Sawyer + Devin

---

## 1. Problem Statement & Goals

Pairing a workstation with a storage server over Syncthing means: install the daemon,
get its systemd *user* service running, find the local API's address and key, add the
server's device ID, create or share a folder with it — and then still approve both on
the server. Each step has a different failure mode, and syncthing's REST API makes two
of them silent: a folder's `devices` entry naming a device not yet in the config is
dropped without an error, and an unrecognised folder `type` is stored as
`sendreceive`. A wizard that trusts the 200s reports success for a pairing that does
not exist.

`devstuff install syncthing` puts the official v2 build on the machine and enables the
user service. `devstuff configure syncthing` then does the pairing through the local
REST API, checking the stored config afterwards because the API's response code does
not say what it stored.

**Success criteria**

- Install gives a working syncthing v2 with its user service enabled where a user
  session exists — and does not fail where there is none (CI container).
- The wizard pairs this machine with a server device and shares exactly one folder,
  verified by re-reading the stored config.
- Nothing is written until the user confirms a review screen.
- The API key never appears in output, errors, logs or a process command line.
- Zero new runtime dependencies.

**Non-goals**

- Approving anything on the server side. The remote end is a human's GUI step; the
  wizard prints the instructions and this machine's Device ID.
- `sendonly` and `receiveencrypted` folder modes, multiple folders per run,
  introducer/auto-accept, and every other syncthing setting.
- The `syncthing@<user>` system unit — the wizard manages the packaged *user* unit
  only.
- Removing syncthing's apt repo, config or keys on `remove` (see SD-6).

---

## 2. Functional Requirements

### Catalog entry (`tools.yaml`)

- **FR-1** `syncthing` is a `bash`-type tool that installs from the official
  `apt.syncthing.net` repo (`stable-v2`), not the Ubuntu archive (1.29).
- **FR-2** The install enables and starts the `syncthing.service` *user* unit when a
  systemd user session exists, and succeeds with a printed hint when it does not.
- **FR-3** `remove` stops/disables the user service and removes the package, and says
  what it left: the config/keys in `~/.local/state/syncthing`, the apt source and
  keyring, and linger.

### Model (`configure/syncthing/model.py`) — pure, no I/O

- **FR-4** `device_id_problem()` validates format, charset (A–Z, 2–7), length (56
  after normalising) and the four Luhn mod-32 check digits, returning a
  human-readable reason or `None`. `normalize_device_id()` uppercases, strips
  whitespace/dashes and regroups into 8×7.
- **FR-5** `paths_overlap()` is true for equal paths or one inside the other, on
  resolved paths; a shared path *prefix* is not overlap.
- **FR-6** `plan()` takes the `GET /rest/config` dict and a `Request` and returns a
  `Plan` (`add_device`, `folder_action` ∈ create/share/none, review lines) or a
  `Refusal`. Refusals: the server ID equals the local ID; a folder with the same ID
  exists with a different resolved path or type (edit it in the GUI); the folder path
  overlaps another folder's.
- **FR-7** `build_device()` fills the `defaults/device` template with the server ID,
  name, `addresses=["dynamic"]`, `introducer=false`, `autoAcceptFolders=false`.
  `build_folder()` fills `defaults/folder` for create, or appends the server device
  to the existing folder for share; the result always lists local + server devices.
- **FR-8** `verify()` re-checks a re-read config: the device is present, the folder
  is present with the asked-for resolved path and type, and the server is in the
  folder's devices. Problems name the silent-drop/coercion behaviour they catch.

### API (`configure/syncthing/api.py`)

- **FR-9** `discover(config.xml)` parses the `<gui>` element into an `Endpoint`:
  `0.0.0.0`/`[::]` map to loopback; `tls="true"` produces https with verification
  off, only for a loopback host; `unix://` and non-loopback hosts return a reason
  string, not an endpoint.
- **FR-10** `Client` uses stdlib `urllib` with `ProxyHandler({})` (proxies must not
  see the key) and a 5s timeout. The key goes only in the `X-API-Key` header.
  `Endpoint.__repr__` redacts it; `ApiError` carries status and body, never the key.
- **FR-11** `wait_ready()` polls `system/status` until `myID` appears or the timeout
  expires.

### Detection (`configure/syncthing/detect.py`)

- **FR-12** `config_file()` parses `syncthing paths`, falling back to
  `$XDG_STATE_HOME/syncthing/config.xml`, then legacy `~/.config/syncthing/` if it
  exists. `user_session()`, `service_active()`, `has_systemd()` and
  `linger_enabled()` probe via `generic._probe`; `None` from `linger_enabled`
  means "cannot tell".

### Wizard (`configure/syncthing/wizard.py`)

- **FR-13** `run(*, target=None)` refuses `--output` (there is no file to export)
  and refuses euid 0 (syncthing is a per-user service).
- **FR-14** Without systemd or a user session it explains and stops. An inactive
  service is offered to be started (`systemctl --user enable --now`), and the API is
  then waited on.
- **FR-15** If linger is off the wizard explains it (sync while logged out, needs
  sudo), asks (default Yes), runs `sudo loginctl enable-linger <user>`, and
  re-checks. `None` stays quiet; declining continues.
- **FR-16** Prompts: server Device ID (locally validated, re-prompted; own ID
  rejected), server name (skipped if the device exists, default "Storage server"),
  folder ID (required, exact-match with the server), local path (default
  `~/SyncthingShared`, expanded, must be absolute), mode picker.
- **FR-17** A review screen lists the planned changes as plain text; nothing is
  written before the confirm. An already-configured pairing skips the write and goes
  to the summary.
- **FR-18** The apply creates the folder directory, POSTs the device then the
  folder (order is load-bearing, FR-8/SD-2), and a mid-apply `ApiError` reports what
  was and wasn't written.
- **FR-19** Afterwards the config is re-read and `verify()`ed; problems are errors,
  not warnings. On success the summary prints the hostname, this machine's Device
  ID, folder ID/path/mode, the server-side approval steps and the GUI URL, and a
  `restart-required` answer is acted on with a confirm.

---

## 3. Non-Functional Requirements

- **NFR-1** Zero new runtime dependencies; stdlib `urllib`, `xml.etree` only.
- **NFR-2** Unit tests need neither syncthing nor the network; the real-binary tests
  `skipif` no `syncthing` binary and run throwaway instances under a tmp `--home`.
  They never touch `~/.local/state/syncthing` or the running service.
- **NFR-3** All subprocess calls go through `generic._run`/`_probe`; all text from
  outside (API errors, device IDs, reasons) goes through `rich.markup.escape` or a
  `Text` before Rich sees it.
- **NFR-4** The API key is never printed, logged or passed on a command line.

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should the wizard offer to install syncthing when it is missing? | **Resolved 2026-10-12 — no extra handling.** The generic command layer already warns + offers (`configure_cmd` checks `is_installed`), and the wizard stops at "no config file" anyway. |
| 2 | Should `--output` export something anyway (e.g. a device+folder JSON)? | **Resolved 2026-10-12 — no.** The artifact would be a script input for `curl`, not a config file; the API is the only writer. |
| 3 | Should the wizard automate the server-side approval? | **Open.** Needs credentials on the server; out of scope for v1. |

---

## 5. Findings That Changed the Design

Measured against syncthing v2.1.6 on two throwaway instances — each one drove a part
of the design rather than being trusted:

- **The collection `POST` endpoints upsert by id; `PUT /rest/config/folders/<id>`
  404s on an id containing `/`**, even percent-encoded. All writes use POST; the
  user's real folder id contains a slash.
- **A folder `devices` entry for a device not in `config.devices` is silently
  dropped with a 200.** Hence the write order (device first) and `verify()`'s
  re-read — "the API took it" and "the config stored it" are different facts.
- **`type: "bogus"` is stored as `sendreceive`, not rejected.** The mode picker and
  the verify step both exist because of this.
- **Nested and identical-overlapping folder paths are accepted** and sit `idle`.
  The overlap check is entirely the wizard's.
- **Relative and `~/` folder paths are accepted and resolved against the daemon's
  working directory** — the wizard expands and resolves the path itself and always
  sends an absolute one.
- **Device-ID validation is local-worthwhile:** the API rejects bad IDs with a 400,
  but only after the write attempt; the wizard catches typos (charset, length,
  check digit) in the prompt loop.
- **`GET /rest/config/defaults/{device,folder}` return usable templates** — the
  folder defaults already include the local device — so `build_*` fills templates
  rather than authoring dicts from scratch.
- **`serve` detaches its worker into its own session.** Killing the monitor process
  leaves the worker running; the live-test fixture cleans up by the unique `--home`
  path instead.
- **Both instances generated before either listens can be probed onto the same
  port.** The fixture pins each throwaway daemon's `listenAddress` to a distinct
  free port.
