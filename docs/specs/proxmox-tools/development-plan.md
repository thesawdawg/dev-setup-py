# Development Plan: Proxmox management functions

**Date:** 2026-09-16
**Status:** Milestones 1–6 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Verified vocabulary | `configure/proxmox/model.py` | Every API path, query parameter and required privilege extracted from Proxmox's own `apidoc.js`; every CLI form taken from the generated man pages (SD-6) |
| 2 | Wiring | `Configurator.tool`, `configure --export`, `DEVSTUFF_BIN` | A configurator with no catalog tool neither claims to be installed nor offers to install itself; a function can call back into devstuff when run from source |
| 3 | Transport | `configure/proxmox/lib.sh`, `fmt.py` | One helper serves both transports; no secret reaches `argv`; `pve_selftest` reports machine-readably |
| 4 | Profiles | `configure/proxmox/{render,detect,validate}.py` | Config round-trips; secrets land in `0600` files outside it; the connection test runs `lib.sh` rather than a second client (SD-4) |
| 5 | Wizard | `configure/proxmox/wizard.py`, registry entry | Add/edit/remove/default/test, diff on overwrite, backup on save, nothing written until confirmed |
| 6 | Functions | 14 `pve-*` entries in `functions.yaml`, README, CLAUDE.md, this spec | Each one resolves a guest by name, prints its command, and confirms before changing anything |

## Testing Strategy

The constraint that shaped everything: **there is no Proxmox host to test against.** The
answer is to put one on `PATH`.

**`tests/test_configure_proxmox.py` — the Python half:**

- **Model invariants.** Every entry in `OPERATIONS` names a real API path or CLI form; read
  operations carry the privilege the API schema says they need; write operations are marked
  SSH-only; keys are unique and match the `pve-` prefix.
- **`OPERATIONS` and `functions.yaml` agree, in both directions.** Every operation has a
  function; every `pve-*` function is an operation; categories, parameter names and required
  flags match. The same shape as `test_ci_matrix_covers_every_builtin_tool`, and for the same
  reason — the drift is invisible until someone tries the command.
- **Rendering.** Config round-trips through `to_yaml`/`load`; an added profile preserves the
  others; the file is written `0600`; **a secret value never appears in the rendered YAML**,
  asserted by searching the output for it.
- **Secret handling.** Resolution from env var, from file, a missing env var, a missing file,
  and a file with loose permissions (warned, not silently used).
- **The shell export.** Values are shell-quoted; a host containing a space or a quote
  survives an `eval` round-trip; `PVE_SECRET_*` names a reference and never a value.
- **Validation.** Malformed `token_id`, unknown transport, missing host, `key` auth with no
  identity file, and an unknown field each raise with the profile named.
- **`fmt.py`** against captured API-shaped JSON: byte and duration rendering, sorting,
  empty-result text, ambiguous-name resolution, and the `qemu`/`lxc` split.

**`tests/test_proxmox_functions.py` — the shell half.** A fixture puts stub `ssh` and `curl`
scripts early on `PATH` and points `DEVSTUFF_BIN` at a stub exporter, so every function runs
end to end without a network:

- `bash -n` over `lib.sh` and all fourteen `script:` bodies.
- `pve-guests`, `pve-status`, `pve-storage`, `pve-snapshots`, `pve-backups` produce a table
  from canned JSON, over **both** transports — the stub `curl` asserts the request it was
  handed, including that the credential arrived on stdin and not in `argv` (NFR-3).
- Guest resolution: by name, by VMID, an unknown name (exit 2), and an ambiguous name (both
  candidates listed, exit 2, **nothing run** — asserted by the stub recording its calls).
- A write operation on an `api` profile refuses and names the transport.
- A write operation with no TTY refuses rather than hanging, and runs nothing; with
  `DEVSTUFF_PVE_ASSUME_YES=1` it runs exactly the command it printed — compared
  string-for-string against the stub's recording.
- `qm` vs `pct` selection, including `--online` for a VM migration and `--restart` for a
  container's, and `--vmstate` offered for one and not the other.
- Exit-code discipline: "no snapshots" and "no updates" exit 0 (FR-21); an unreachable host
  exits non-zero.

**Not covered, and deliberately:** that a real Proxmox node accepts these commands. The
stubs prove what devstuff sends, never what Proxmox does with it. `pve-check` and the
wizard's connection test exist to close that gap on the user's own host, on first use.

## Risks

| Risk | Mitigation |
|------|------------|
| **No live verification.** The commands are right per the man pages and the schema, but nothing has run them against a node. | `pve-check` verifies a profile end to end against the user's host, including which remote binaries exist. Every operation prints its command before running it, so a wrong one is visible before it executes, not after. |
| **A destructive command against the wrong guest.** | Name resolution never guesses (SD-9); the resolved VMID, node and name are printed in the confirmation; the default answer is No. Create/destroy are out of scope entirely. |
| **Proxmox changes a CLI flag** — `pct migrate --restart` and `qm migrate --online` are already inconsistent. | The asymmetries are in `OPERATIONS` with the man page they came from, in one table, rather than spread through fourteen scripts. |
| **A secret leaking into a log.** | Never in `argv` (NFR-3), never in the profile file (FR-5), never in the `--export` bridge (SD-3); a test greps the rendered output for the value. |
| **`sshpass` absent for password auth.** | Guarded with the apt package name, the way `whats-on-port` handles `iproute2`; the wizard warns at the point the user chooses password auth. |
