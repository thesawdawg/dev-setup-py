# Specification: Proxmox management functions + `devstuff configure proxmox`

**Date:** 2026-09-16
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

Managing a Proxmox VE host from a workstation means remembering a command surface that is
split across five binaries and does not agree with itself:

| you want to                  | for a VM                              | for a container                        |
|------------------------------|---------------------------------------|----------------------------------------|
| live-migrate                 | `qm migrate 101 pve2 --online`        | **`pct migrate 101 pve2 --restart`**    |
| migrate to other storage     | `qm migrate … --targetstorage local`  | **`pct migrate … --target-storage local`** |
| snapshot including RAM       | `qm snapshot 101 s1 --vmstate 1`      | **no such option — containers cannot** |
| list them                    | `qm list`                             | `pct list`                             |

None of those asymmetries is guessable, all four are load-bearing, and the first thing any
of these commands needs is a VMID — a number nobody remembers for the guest they actually
mean. The usual result is `ssh root@node`, `qm list | grep`, read the number off, retype the
command against the wrong one of `qm`/`pct`, and get `Configuration file 'nodes/pve1/qemu-server/104.conf' does not exist`.

This adds a `proxmox` family of `devstuff run` functions that take a **guest name or VMID**,
work out which node it is on and whether it is a VM or a container, **print the exact
`qm`/`pct`/`vzdump`/`pvesh` command they are about to run**, and ask before running anything
that changes state. Plus `devstuff configure proxmox`, which stores the connection profiles
they read.

The printed command is the point, not decoration: the functions are meant to leave the user
able to type the command themselves next time, and answering `n` at the confirmation leaves
them holding exactly that.

**Success criteria**

- A guest is addressable by name; the VM/container split and the node it lives on are
  resolved, never asked for.
- Every state-changing operation prints its exact remote command and is confirmed first.
- No secret is written to the profile file, passed in `argv`, or printed.
- Read operations work over an API token with no shell access to the node.
- Zero new runtime dependencies (NFR-1), and no dependency on `jq`/`yq` (SD-5).

**Non-goals**

- **Creating or destroying guests.** `qm create`/`destroy`, `pct create`/`destroy` and
  snapshot deletion are out of scope in v1. Provisioning wants a template/cloud-init model
  this does not have, and destruction is the one thing a wrong VMID makes unrecoverable.
- **Writes over the API transport.** SSH is the write path (SD-3).
- **Cluster administration** — joining/leaving nodes, HA rules, replication jobs, firewall,
  users and permissions. Read-only visibility of cluster health is in scope; changing it is
  not.
- **Proxmox Backup Server.** PBS-backed storages appear in listings like any other storage;
  `proxmox-backup-client` is not wrapped.
- **Installing anything on the node.** The functions assume a working PVE host.

---

## 2. What was measured, and what was not

The repo's rule for configurators is *measure the tool, don't recall it*. No Proxmox host
was reachable from the development environment, so the usual method — interrogate the
installed binary — was not available. What replaced it:

- **The API surface was measured, not recalled.** `pve.proxmox.com/pve-docs/api-viewer/apidoc.js`
  is the API schema Proxmox generates from its own source (454 paths). Every path, query
  parameter, returned field name and **required privilege** in `model.py` was extracted from
  it programmatically, not typed from memory. `docs/specs/proxmox-tools/stack-decisions.md`
  SD-6 records what that turned up.
- **The CLI surface came from the generated man pages** (`qm.1`, `pct.1`, `vzdump.1`,
  `pvesh.1`), which is where the four asymmetries in the table above came from.
- **Not measured: any behaviour of a live node.** Whether a given node answers, what its
  token can actually do, and whether the remote binaries exist are *checked at run time
  against the user's own host* instead — by `pve-check` and by the wizard's connection test,
  which run the real transport code (FR-24).

The honest summary: the command and API vocabulary is verified; the round trip is not, and
is verified on first use by the user instead of being asserted here.

---

## 3. Functional Requirements

### Profiles

- **FR-1** Connection details live in `~/.config/devstuff/proxmox.yaml`, mode `0600`, a
  `version: 1` document holding a map of named profiles and one `default`.
- **FR-2** A profile's `transport` is `ssh` or `api`. Both are offered; the user chooses per
  profile, and may have any mix.
- **FR-3** SSH profiles carry `host`, `user` (default `root`), `port` (default 22) and
  `auth`: `agent`, `key` (with `identity_file`) or `password`.
- **FR-4** API profiles carry `host`, `api_port` (default 8006), `token_id` of the form
  `USER@REALM!TOKENNAME`, and `verify_tls` (default true).
- **FR-5** **No secret is ever written to `proxmox.yaml`.** A profile names where its secret
  lives — `secret_env: NAME` (an environment variable) or `secret_file: PATH` (a file the
  wizard writes at mode `0600` under `~/.config/devstuff/secrets/`) — and nothing else.
- **FR-6** A profile is validated on load: unknown fields, an unknown transport, a
  malformed `token_id`, a missing host, and a `key`/`password`/`api` profile with no secret
  reference are all load-time errors naming the profile, consistent with the rest of
  devstuff's catalogs.
- **FR-7** The profile used by a function is: its `profile` parameter, else
  `$DEVSTUFF_PVE_PROFILE`, else the config's `default`.

### The wizard

- **FR-8** `devstuff configure proxmox` adds, edits, removes and re-defaults profiles, and
  writes nothing until the user confirms. It is a **standalone configurator** — there is no
  `proxmox` package to install, so the install-state gate is skipped rather than lying about
  it (FR-22).
- **FR-9** It reports, before asking anything: whether `ssh`, `curl` and (for password auth)
  `sshpass` are present locally, which profiles exist, and whether each one's secret
  currently resolves.
- **FR-10** It offers to test a profile's connection, and does so **by running the same
  transport code the functions use** (SD-4) — not a second implementation.
- **FR-11** For an API profile the test reports, per privilege, what the token can actually
  do: which read operations it is allowed and which it is not, naming the privilege each
  needs.
- **FR-12** Choosing `password` auth warns that it needs `sshpass`, that the password goes
  into the environment of a child process, and that key auth is the better answer; it does
  not refuse.
- **FR-13** Overwriting a config the wizard did not write shows a unified diff first, and
  every save keeps a timestamped backup.

### The functions

- **FR-14** Fourteen `pve-*` functions in category `proxmox`, listed in `model.OPERATIONS`,
  which is the single table both the functions and the tests are checked against (FR-23).
- **FR-15** Read operations — `pve-check`, `pve-status`, `pve-guests`, `pve-storage`,
  `pve-updates`, `pve-backups`, `pve-snapshots` — work on **both** transports.
- **FR-16** State-changing operations — `pve-start`, `pve-stop`, `pve-restart`,
  `pve-snapshot`, `pve-rollback`, `pve-backup`, `pve-migrate` — require an `ssh` profile and
  refuse on an `api` profile with a message naming the profile and what to do about it.
- **FR-17** A guest argument is a **name or a VMID**. Resolution reads
  `/cluster/resources?type=vm` once and yields the VMID, the node, and whether it is `qemu`
  or `lxc`; the right binary (`qm` or `pct`) follows from that, never from the user.
- **FR-18** An ambiguous name — two guests sharing it — lists the candidates with their
  VMIDs and nodes and exits non-zero. It never picks one.
- **FR-19** Every state-changing operation prints the exact command it will run, on one
  copy-pasteable line, **before** the confirmation prompt.
- **FR-20** Confirmation requires stdin **and** stdout to be a TTY. Without both, the
  operation is refused, never auto-approved and never left hanging on an unreadable prompt
  (SD-7). `DEVSTUFF_PVE_ASSUME_YES=1` is the documented, deliberate escape hatch.
- **FR-21** A read operation that finds nothing ("no snapshots", "no updates pending") is a
  success and exits 0. Non-zero is reserved for not being able to perform the lookup at all
  — the rule `whats-on-port` and `which-ansible` already follow.

### Wiring

- **FR-22** `Configurator` gains `tool: str | None`. `None` means "not backed by a catalog
  tool": the install check, the `(not installed)` marker and the `devstuff install` hint are
  skipped for it.
- **FR-23** `configure_cmd` gains a generic `--export [PROFILE]` that prints a configurator's
  shell bridge to stdout and exits. Like `register: eval` functions, **it prints nothing but
  the assignments to stdout**; every diagnostic goes to stderr.
- **FR-24** `pve-check` performs the wizard's connection test from the command line, so a
  profile can be re-verified without opening the wizard.

---

## 4. Non-Functional Requirements

- **NFR-1** No new runtime dependency. The wizard uses PyYAML (already required); the
  functions use `ssh`, `curl` and the Python interpreter devstuff is already running under.
- **NFR-2** No `jq` or `yq`. JSON is parsed by that interpreter, whose path the shell bridge
  exports (SD-5).
- **NFR-3** **A secret never appears in `argv`.** The API token is fed to `curl` through
  `-K -` on stdin; an SSH password is passed to `sshpass -e` through the environment. Neither
  is printed, and neither is written to the profile file (FR-5).
- **NFR-4** The shell helper is sourced from the installed package, never copied into the
  user's config directory, so it cannot drift out of step with the devstuff that ships it
  (SD-2).
- **NFR-5** Every function is exercised by tests against stub `ssh` and `curl` binaries, so
  the parsing, resolution, refusal and exit-code paths are covered without a Proxmox host.

---

## 5. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should `pve-backup` wait for the vzdump task and report its result? | **Resolved 2026-09-16** — no. Over SSH `vzdump` streams its own progress to the terminal and exits with a real status; polling `/nodes/{node}/tasks` would only re-implement that worse. Revisit if an API write path is ever added (SD-3). |
| 2 | Snapshot deletion (`qm delsnapshot`) | Deferred. It is the one lifecycle operation with no undo and no confirmation-proof guard better than "type the name", which v1 does not have. |
| 3 | Should the wizard offer to create the API token on the node? | Deferred. It needs an SSH profile to the same host to run `pveum`, so it is only possible in the case where the user already has the stronger credential. |
| 4 | Multiple hosts per profile (a cluster with a failover list) | Deferred. `/cluster/resources` already answers cluster-wide from any one node; a second host only helps when the first is down, which is a monitoring concern, not this. |
