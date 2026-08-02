# Development Plan: `devstuff configure docker`

**Date:** 2026-08-02
**Status:** Milestones 1–5 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Catalog entry | `docker` in `src/dev_setup/tools.yaml` (`type: bash`) — already present | `devstuff install docker` puts the daemon on the host |
| 2 | Measured settings catalog | `configure/docker/model.py` — `LOG_DRIVERS`, `SETTINGS`, `GROUPS`, `PRESETS`, `DockerConfig` | Every per-driver option set is what the daemon accepted when that option was offered to that driver |
| 3 | Emitter | `configure/docker/render.py` — `data()`, `to_json()`, `matches()`, `diff()` | Every preset round-trips and is accepted by real `dockerd --validate` |
| 4 | Host detection and round-trip | `configure/docker/detect.py` | A realistic hand-written `daemon.json` with unmodelled keys reads in and emits back byte-identical |
| 5 | Checks, writing, wizard | `configure/docker/{validate,wizard}.py`, registry entry, README, CLAUDE.md, this spec | The wizard walks the steps, catches what `--validate` misses, saves through sudo, and offers the restart separately |

## Testing Strategy

**`tests/test_configure_docker.py` — unit by default, with the `dockerd`-dependent tests skipped
when the binary is absent (NFR-2). 90 tests:**

- **Model invariants:** every setting names a real group and a real field on `DockerConfig`;
  `json_key`s are unique; every modelled key appears in the emitter's order table; every preset
  names fields that exist. Plus the one that matters most — **`Setting.default` equals the
  `DockerConfig` field default**, because the emitter omits values equal to `Setting.default`
  and a drift between the two would silently write keys nobody asked for (SD-3).
- **Preset safety:** every preset except `current` and `empty` caps the logs. This is FR-3, and
  it is the single assertion that says the wizard does the thing it exists to do.
- **Driver-specific log options:** `journald` drops the rotation options and says so;
  carried-over options survive when the driver takes them and are dropped when it does not;
  `local` really does accept `tag`/`labels`/`env`. Each of these encodes a measurement that
  contradicted a plausible guess.
- **The five things `--validate` waves through** (SD-1), each its own test: compress with
  `max-file: 1`; an uncapped `json-file`; an address pool narrower than its base; a pool with no
  usable addresses; `hosts` against a systemd unit that passes `-H`. Plus the two negative
  cases that keep the checks honest — `journald` is *not* reported as uncapped, and driver
  availability stays silent when the daemon could not be asked.
- **Emitting:** defaults are omitted rather than restated; address pools come out as JSON lists
  rather than tuples (the thing `matches()` exists to catch); keys are emitted in the table's
  order; unmodelled keys are carried through; `matches()` rejects both wrong content and
  non-JSON.
- **Reading an existing config:** the full round-trip assertion (SD-2); unmodelled keys land in
  `extra`; wrongly-typed values on disk are ignored rather than crashed on; a malformed pool
  entry does not break the read; an invalid or missing file is reported rather than raised.
- **Presets and suggestion:** applying a preset keeps `extra` but clears the previous preset's
  values; `suggest()` adds rotation to an existing config that lacks it, leaves an already-capped
  one alone, and starts from `rotation` on a bare host; a rootless daemon gets its own path.
- **Writing:** a write creates its parent; a backup preserves content; a backup of a missing
  file is not an error; a no-op diff is empty.
- **Against the real binary (skipped without `dockerd`):** every preset passes
  `dockerd --validate`; a config setting *every* modelled key passes; and a key typo is caught —
  the check that justifies still running `--validate` at all rather than replacing it (SD-1).

**Verified by hand, end to end** (the interactive flow cannot be driven from a piped shell — a
`pty.fork()` was used, as for the other wizards):

1. On this host the wizard reported Docker 29.6.2, storage `overlayfs`, log driver `json-file`,
   and warned that container logs are uncapped.
2. Choosing "Log rotation only" and the `local` driver, then accepting `10m` × `3` with
   compression, produced a config that **real `dockerd --validate` accepted**.
3. A container was then started with exactly those settings and **ran successfully** —
   `docker run --log-driver local --log-opt max-size=10m --log-opt max-file=3 --log-opt
   compress=true`.
4. The config the wizard *refuses* (`max-file: 1` with compression) was offered to the real
   daemon and **rejected**: "compress cannot be true when max-file is less than 2". This is SD-1
   demonstrated end to end — `--validate` accepts that file, and the wizard is the only thing
   between the user and a host that cannot start a container.
5. Re-running against the now-existing file showed a unified diff of the changes and asked
   before applying (FR-24).
6. Re-running with an unchanged config reported "already contains exactly this. Nothing to
   write." and wrote nothing.

## Risks

| Risk | Mitigation |
|------|------------|
| **A saved config stops the daemon from starting.** The failure lands after a restart, when the wizard is long gone. | `verify()` runs before every save and combines `--validate` with the checks it lacks (SD-1). The previous file is backed up with a timestamp (FR-23), and a failed restart points at `journalctl -u docker`. |
| **The restart kills running containers.** | It is a separate question stating the container count, and it reads the daemon's *current* live-restore state rather than the new config's (SD-5, FR-25). |
| **A hand-tuned `daemon.json` loses keys the wizard does not model.** | They are preserved in `extra` and written back untouched, asserted by a round-trip test over a realistic config (SD-2). |
| **Docker changes a default and the machine keeps the old one.** | Only non-default values are written, so accepting a default means accepting whatever it becomes (SD-3). |
| **The per-driver option tables go stale as Docker adds options.** | An option the table omits is dropped from the emitted file rather than written blindly, so staleness costs a feature rather than a broken daemon. The tables carry the measurement method in a comment so they can be re-derived. |
| **`sudo` is unavailable or refused.** | `write()` returns `(False, message)` and the existing config is untouched — the temp-file staging means a failed sudo cannot truncate anything (SD-6). |
| **The wizard's checks disagree with a future Docker.** | A failed check warns and asks; it never blocks a save (FR-20). The user's config is the user's to save. |

## Not built

- Starting a container to prove the log options work end to end (Open Question 5).
- `default-ulimits` authoring (Open Question 1) — preserved through `extra` when hand-set.
- Rootless *setup*, as opposed to rootless detection (Open Question 4).
- `~/.docker/config.json`, buildx builders, contexts, and swarm settings (all non-goals).
- A canary that re-derives the per-driver option tables against a current daemon on a schedule.
