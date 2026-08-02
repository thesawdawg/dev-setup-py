# Specification: `devstuff configure docker`

**Date:** 2026-08-02
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

`devstuff install docker` gives you a working daemon with a default that fills disks.

Docker's default log driver is `json-file` with **no size limit**. Every line a container
writes to stdout is appended to a JSON file under `/var/lib/docker` forever. A chatty
container — a dev server in a restart loop, an application logging at debug — grows that file
until the partition is full, at which point the daemon, and usually the host, stops working.
Nothing warns about this. It is the single most common way a Docker host dies.

Fixing it means writing `/etc/docker/daemon.json`, and that file has its own trap: it is
checked by a validator whose coverage stops well short of "this works". Measured against
Docker 29.6:

| config                                            | `dockerd --validate` | what actually happens          |
|---------------------------------------------------|----------------------|--------------------------------|
| `"lof-driver"` (a typo)                            | **rejected**         | —                              |
| `"base": "notanetwork"`                            | **rejected**         | —                              |
| `"max-size": 10` (a number, not a string)          | **rejected**         | —                              |
| `"log-driver": "nosuchdriver"`                     | accepted             | every container fails to start |
| `"log-driver": "local"` + an unknown log option    | accepted             | every container fails to start |
| `max-file: "1"` with `compress: "true"`            | accepted             | every container fails to start |
| `default-address-pools` with `size` below the base | accepted             | no usable networks             |
| `"hosts"` alongside a systemd unit passing `-H`    | accepted             | daemon refuses to start        |

The bottom five share a shape: the daemon starts, reports itself healthy, and every
`docker run` afterwards fails with an error that never mentions `daemon.json`.

And a valid file changes nothing until the daemon restarts — which stops every running
container unless `live-restore` was already on *before* that restart.

**Success criteria**

- A user who has never opened `daemon.json` gets capped container logs, verified against the
  real `dockerd`, in under a minute.
- The wizard catches every failure in the table above that `--validate` does not.
- An existing hand-written `daemon.json` is never silently degraded: unmodelled keys survive
  the round trip byte-for-byte.
- Nothing is written until the user confirms, and nothing is restarted until they confirm
  again, separately.
- Zero new runtime dependencies.

**Non-goals**

- Configuring anything outside `daemon.json`. Docker contexts, `~/.docker/config.json`
  credentials, buildx builders and compose files are separate formats with separate lifecycles.
- Managing containers, images or volumes. This wizard configures the daemon.
- Swarm. `live-restore` is incompatible with swarm mode, which is noted rather than modelled.
- Installing Docker. That is `devstuff install docker`.
- Rootless *setup*. A rootless daemon is detected and its own config path used, but
  `dockerd-rootless-setuptool.sh` is not run.

---

## 2. Functional Requirements

### Catalog and presets

- **FR-1** The daemon settings are data: `LOG_DRIVERS`, `SETTINGS`, `GROUPS` and `PRESETS` in
  `configure/docker/model.py`. Adding a setting is one `Setting` record, and it reaches the
  toggles, the emitter's order table and the review screen with no other edit.
- **FR-2** Eight presets: `rotation`, `workstation`, `server`, `ci-runner`, `journald`,
  `corporate`, `current` and `empty`.
- **FR-3** Every preset except `current` and `empty` caps container logs. Asserted by
  `test_every_real_preset_caps_the_logs` — a preset that leaves logs uncapped is the exact bug
  this wizard exists to prevent.
- **FR-4** `current` starts from the `daemon.json` on disk; it is not offered when there is none.
- **FR-5** Per-driver log options are what the daemon actually accepted when each option was
  offered to each driver, not what the documentation implies. Two contradict the obvious guess:
  `local` does take `tag`/`labels`/`env`, and `journald` does *not* take
  `max-size`/`max-file`/`compress`.

### Host detection

- **FR-6** Before asking anything the wizard reports: whether the CLI and daemon are present,
  the server version, storage driver, current log driver, live-restore state, running container
  count, the existing `daemon.json`, docker group membership, and whether systemd manages the
  daemon.
- **FR-7** A rootless daemon is detected from `docker info`'s `SecurityOptions` and its config
  path (`~/.config/docker/daemon.json`) used instead of `/etc/docker/daemon.json` — a rootless
  daemon never opens the latter, so writing there would need sudo for a file nothing reads.
- **FR-8** The set of loadable log drivers is read from `docker info`'s `Plugins.Log` at run
  time rather than hardcoded, so a host with a plugin the wizard has never heard of is not told
  its driver is wrong.
- **FR-9** `none` is exempt from FR-8's check. Measured: it is built into the daemon and never
  appears in the plugin list, but `docker run --log-driver none` works.
- **FR-10** A `daemon.json` that exists but is unreadable (root-owned, not world-readable) is a
  normal state and is reported as such, never as an error.
- **FR-11** A `daemon.json` that is not valid JSON is reported as such, with the note that the
  daemon cannot be reading it.

### Generated config

- **FR-12** Only settings differing from the daemon's own defaults are written. A file
  restating the defaults freezes them, so a future Docker changing one would never reach the
  machine.
- **FR-13** Every value in `log-opts` is written as a string. This is the one type error
  `--validate` catches, and it catches it by refusing to start the daemon.
- **FR-14** `log-opts` is filtered to what the chosen driver accepts. Switching to `journald`
  drops the rotation options rather than writing options that make every container fail.
- **FR-15** Keys read from an existing `daemon.json` that the wizard does not model are carried
  through to the output untouched, and shown on the review screen as carried over.
- **FR-16** The emitted JSON must parse back to exactly what `render.data()` describes, checked
  by `render.matches()` for every preset. What this really asserts is that `data()` emits
  JSON-native types — address pools are tuples in the model and must be lists in the file.

### Verification

- **FR-17** `validate.verify()` runs `dockerd --validate --config-file` against a throwaway
  copy. It only reads the file and starts nothing, so validating never affects a running daemon.
- **FR-18** `verify()` additionally performs every check in the problem-statement table that
  `--validate` does not: the driver exists, the log options suit the driver, compression is
  usable, the logs are capped, address pools can produce a network, and `hosts` does not
  collide with the systemd unit.
- **FR-19** A machine without `dockerd` still gets the model checks; the dockerd check reports
  itself skipped rather than failed.
- **FR-20** A failed check at save time is reported and asks for confirmation. It never blocks
  the save.
- **FR-21** After a restart, the daemon is asked what it now believes and the answer compared
  to what was written — the only proof the file reached it.

### Writing and applying

- **FR-22** `/etc/docker/daemon.json` is root-owned, so writing goes through
  `sudo install -m 0644 -o root -g root`, staged via a temp file. The exact command is printed
  before it runs. A partial or failed write leaves the existing config untouched.
- **FR-23** An existing file is copied to `<name>.bak.<timestamp>` first, on every path
  including `--output`.
- **FR-24** Overwriting shows a unified diff of what changes and asks. A candidate identical to
  what is already there is reported as such and nothing is written.
- **FR-25** The daemon restart is a separate question asked after the save, stating the running
  container count, and it reads the daemon's *current* live-restore state rather than the new
  config's — live-restore only protects a restart if it was already on.
- **FR-26** `restart` is `systemctl restart docker`, deliberately not `reload`: reload re-reads
  only a subset of `daemon.json` and which subset is not something to guess at.
- **FR-27** Adding the user to the `docker` group is offered once, after the save, with the note
  that it only applies to new logins and that group members can run containers as root.
- **FR-28** `--output <path>` writes elsewhere and never touches the daemon: no sudo, no
  restart, no group change.

---

## 3. Non-Functional Requirements

- **NFR-1** Zero new runtime dependencies. `json` and `ipaddress` are stdlib.
- **NFR-2** The unit test suite requires neither a Docker daemon nor the network. Tests needing
  `dockerd` are skipped when it is absent.
- **NFR-3** No import of the wizard at CLI start-up — loaded on demand through
  `configure.Configurator.load()`.
- **NFR-4** `verify()` completes in well under a second.
- **NFR-5** Every failure path in `detect.py` and `validate.py` returns an empty field or a
  failed `Check`. A verification must never be able to end the wizard.
- **NFR-6** Nothing in `detect.py` writes anything. The whole module is read-only.

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should the wizard offer `default-ulimits`? | **Resolved 2026-08-02 — no.** Its value is a nested `{Name, Hard, Soft}` object per limit, which is a form rather than a decision. A hand-set one is preserved through `extra`. |
| 2 | Should it manage `~/.docker/config.json` too? | **Resolved 2026-08-02 — no.** That is the client's file, holds credentials, and has nothing to do with the daemon's behaviour. |
| 3 | Should the daemon restart be automatic after a save? | **Resolved 2026-08-02 — no.** A restart stops running containers unless live-restore was already on. That cost belongs to an explicit question. |
| 4 | Should a rootless daemon be *set up* by the wizard, not just detected? | **Open.** `dockerd-rootless-setuptool.sh install` is arguably an install-time concern for `tools.yaml` rather than a configuration one. |
| 5 | Should `verify()` try starting a container to prove the log options work? | **Open.** It is the only thing that would prove FR-18's checks end to end, but it needs a running daemon, an image, and permission — far past what a config wizard should assume. |

---

## 5. Findings That Changed the Design

- **`dockerd --validate` accepts five configs that break every container.** The whole shape of
  `validate.py` — a wizard check set that runs *alongside* the real validator rather than
  deferring to it — comes from measuring this rather than assuming the validator was
  authoritative.
- **`compress: "true"` with `max-file: "1"` is rejected by the daemon, not the validator.** The
  error is "compress cannot be true when max-file is less than 2 or max-size is not set". The
  wizard now only offers compression when it can work.
- **`journald` rejects `max-size`/`max-file`/`compress`.** Discovered by offering every option
  to every driver. This is why `log_opts()` filters by driver rather than emitting whatever was
  set, and why switching driver reports what it dropped.
- **`local` accepts `tag`, `labels` and `env`.** The opposite of the obvious guess, which is
  that the compact driver takes only the rotation options.
- **`none` is not in `docker info`'s `Plugins.Log`.** It is built into the daemon. Caught when
  the availability check called a working driver unavailable — hence `BUILTIN_DRIVERS`.
- **Modern Docker reports its storage driver as `overlayfs`, not `overlay2`.** Measured on 29.6.
  A hardcoded "the default is overlay2" would already be wrong, which is why the wizard reports
  the driver rather than asserting a default for it.
- **The packaged systemd unit passes `-H fd://`.** So a `hosts` key in `daemon.json` — legal,
  and accepted by `--validate` — makes the daemon refuse to start. Detected by reading
  `systemctl cat docker.service` rather than assumed.
