# Stack Decisions: `devstuff configure syncthing`

**Date:** 2026-10-12
**Context:** The ninth configurator, and the first that configures a *running service*
through its REST API instead of writing a config file. SD-1/SD-2 in
`docs/specs/starship-config/` (configurators are Python modules in a registry, keyed
in Python rather than declared in `tools.yaml`) apply unchanged and are not restated.

---

## SD-1 — The local REST API via stdlib `urllib`, not `syncthing cli`

**Decision: all daemon interaction is HTTP against `127.0.0.1` with the `X-API-Key`
header, using `urllib` with a `ProxyHandler({})` opener.**

The REST API is the documented, versioned contract — syncthing's own GUI is just a
client of it. `syncthing cli` exists, but its argument and JSON shapes change between
versions, it shells out per call, and each invocation would carry the API key as a
command-line argument — visible in `ps` and in devstuff's own `-vv` command log, which
is exactly where this project's subprocess helpers put argv.

- **Rejected — `syncthing cli` subcommands.** Less stable surface, and the key on argv.
- **Rejected — a HTTP library (requests/httpx).** A new runtime dependency for one wizard;
  `urllib` is sufficient and keeps NFR-1.
- **Rejected — the default urllib opener.** It honours `http_proxy`, and a corporate proxy
  must never see the local daemon's API key — hence the explicit empty `ProxyHandler`.

**Consequence:** `api.py` is ~120 lines and the key exists in exactly one place — the
header set in `Client._request`. `Endpoint.__repr__` redacts it so a traceback or a
`verbose` dump can't leak it.

## SD-2 — `POST` to the collection endpoints, never `PUT …/<id>`

**Decision: device and folder writes are `POST /rest/config/devices` and
`POST /rest/config/folders` with the id inside the body.**

Measured on v2.1.6: the collection POSTs upsert by id and accept any id — space, `?`,
`#`, `%`, unicode and `/` all work. `PUT /rest/config/folders/<id>` returns 404 for an
id containing `/`, even percent-encoded, because the id is a path segment. The folder
id this was built for (`shared/…`) hits exactly that.

- **Rejected — `PUT` for "update" and `POST` for "create".** The put-by-id route is the
  broken one; the collection POST already upserts, so a second code path is risk for
  nothing.

## SD-3 — The official apt repo (stable-v2), not the Ubuntu archive

**Decision: the install adds `apt.syncthing.net` (`syncthing stable-v2`) with the
upstream release key, modelled on the `gh` entry.**

The Ubuntu archive ships 1.29 — a major version behind v2, with a different on-disk
config format (`~/.config/syncthing` vs `~/.local/state/syncthing`) and a different
REST surface. The user chose v2 deliberately.

- **Rejected — the Ubuntu archive.** Ships 1.x; "simpler" only until a mixed-v1/v2 pair
  has to be debugged.
- **Rejected — a pin in `/etc/apt/preferences.d`.** Deliberately not added: the repo's
  2.x already outranks the archive's 1.29, so a pin would be a file doing nothing.
  Revisit if Ubuntu ever ships a higher-versioned build.

## SD-4 — Linger is asked about in the wizard, never set by the install

**Decision: `devstuff install` never touches `loginctl`; the wizard detects linger is
off, explains what it does, and asks (default Yes).**

`loginctl enable-linger` needs sudo and changes a system-wide property (the user's
services run without a login). An installer doing it unprompted is a machine-policy
change hidden inside an app install; a wizard asking about it is a decision.

- **Rejected — enable linger in `install_script`.** Same sudo-and-system-wide cost, no
  user consent.
- **Rejected — do nothing.** Then sync silently stops whenever the user logs out, which
  is most of the point of a storage pairing on a headless-leaning machine.

**Consequence:** `detect.linger_enabled()` has a real three-state answer — `None`
("loginctl can't say") stays silent rather than warning about a state it can't see,
the same contract as the Nerd Font check.

## SD-5 — The packaged systemd *user* unit, not `syncthing@user`

**Decision: the install and the wizard both manage `syncthing.service` under
`systemctl --user`; the system unit is not touched.**

The Debian package ships `/usr/lib/systemd/user/syncthing.service`
(`ExecStart=/usr/bin/syncthing serve --no-browser --no-restart`). A user unit runs as
the user, keeps config in `~/.local/state/syncthing`, needs no root to start, and pairs
with linger to survive logout. `syncthing@<user>` runs the daemon as root-managed
system service — a different operating model for a machine where syncthing is one
user's tool.

- **Rejected — `systemctl enable syncthing@<user>`.** Needs root for every management
  operation and makes the daemon's files root-owned. Wrong shape for a dev tool.
- **Rejected — "just run `syncthing` in a shell".** Then nothing survives reboot or
  logout; the wizard would be pairing a daemon that isn't running half the time.

**Consequence:** the wizard refuses to run as root (there is no root user service to
manage), and a host with no systemd user session gets an explanation rather than a
failure.

## SD-6 — `remove` leaves the apt repo, config and linger in place

**Decision: `remove_script` stops/disables the user unit and removes the package;
the apt source, keyring, `~/.local/state/syncthing` and linger are left and said to
be left.**

The apt repo may predate this install — removing it could break another syncthing the
user installed by hand. The config dir holds the device's identity (key pair); deleting
it makes a reinstall look like a new device to every peer. Linger is a per-user system
property this tool only *asked* about.

- **Rejected — `apt-get remove` plus wiping the source/keyring/config.** Destroys
  device identity and possibly a repo the user had anyway.

## SD-7 — `--output` is refused

**Decision: `run(target=…)` prints an explanation and returns None.**

Every other configurator writes a file, so `--output` means "write it elsewhere". Here
there is no file: syncthing is configured through its running API. Silently treating
`--output` as "POST to a different daemon" would be a trap, and exporting a JSON blob
would produce an artifact nothing consumes.

## SD-8 — The apply path takes an explicit `Client`

**Decision: `wizard.apply(client, config, local_id, req, plan)` is a function the
wizard calls and the live test calls directly.**

The prompts and systemd steps can't be exercised without a pty, but plan → POST order
→ verify can — against two real throwaway daemons. Factoring the writes out of `run()`
is what makes the live test test the same code path the wizard runs, rather than a
reimplementation of it.
