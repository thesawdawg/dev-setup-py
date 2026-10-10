# Development plan: `devstuff outdated`

**Date:** 2026-10-10
**Status:** Approved (2026-10-10) — milestones 1–2 done

---

## Milestones

| # | Milestone | Satisfies | State |
|---|-----------|-----------|-------|
| 1 | Fix redundant `uv` probes: lock/prime `_uv_outdated_map`, parse `uv tool list` once for all lookups | FR-19, NFR-2 | **Done** 2026-10-10 |
| 2 | Move the collector out of `update_cmd.py`; type it against `GenericTool`; drop the `type: ignore` | FR-15–17, SD-4 | **Done** 2026-10-10 |
| 3 | State classifier: `UpdateStatus` + `install_type` → one of five states | FR-5–8, SD-2/3 | Not started |
| 4 | `commands/outdated_cmd.py`: table, footer, summary, flags, exit status; register in `cli._register_commands` | FR-1–4, 9–11, 13–14, 18 | Not started |
| 5 | `--json` output and stdout-purity test at `-vv` | FR-12, NFR-1 | Not started |
| 6 | `UpdateStatus.note`: add the field; set it in the uv checker (F-2) and on the npm/apt/git network-failure paths; render and emit it | FR-23 | Not started |
| 7 | README (command reference), CLAUDE.md (architecture line), roadmap + specs index | — | Not started |

Milestones 1 and 2 land first and **before any new command exists**: both are changes to
existing behaviour, so each is verifiable against `update`'s current output. Doing them first
means the new command is built on a corrected, shared base rather than copying the race.

M3 is deliberately a pure function with no I/O, so the five-state rule — the point of the
spec — is testable without a terminal or a network.

## Testing strategy

New file `tests/test_outdated.py`. Unit-only; no network and no real installs, so it stays in
the default ~0.3 s suite.

- **Classifier, exhaustively.** Parametrized over `(install_type, UpdateStatus)` → state: each
  of the five states, plus the two that matter most — a `bash` tool and a failed `npm` probe both
  have an empty status and must classify as `unsupported` and `unknown` respectively (SD-2).
- **Rendering distinguishes states** (FR-7/FR-20): capture the Rich output and assert the
  `unknown` and `unsupported` renderings contain neither the `current` glyph nor its wording.
  This is the test that fails if someone "tidies" the states back together.
- **`note` is optional and never invented** (FR-23): a checker that sets nothing yields an empty
  note and unchanged output; the uv checker sets one for a tool on `PATH` but absent from
  `uv tool list` (the `commitizen` case, F-2).
- **Counts sum to tools checked** (FR-11), including with `--updates-only` hiding rows.
- **Read-only** (FR-3): patch `install`/`remove`/`update` on the registry's tools to raise; the
  command must complete.
- **Exit status** (FR-14): exit 0 with outdated tools present; exit 1 for an unknown key.
- **`--json` purity** (FR-22/NFR-1): run at `-vv` with `capfd`, assert stdout parses as a JSON
  array and every record has exactly the six fields (`key`, `type`, `state`, `installed`, `latest`, `note`).
- **Collector parity** (FR-21): with probes stubbed, the shared collector returns what the
  pre-move `_collect_update_candidates` did, and `update`'s picker is unchanged.
- **Probe count** (FR-19): stub `generic._probe` with a counter, run the collector over N≥3
  fake uv tools concurrently, and assert `uv tool list` and `uv tool list --outdated` each ran
  once (`tests/test_uv_probes.py`). This test must fail on the current code — it is the regression test for finding F-1,
  and a version that passes before the fix proves nothing.

**Live verification** (not in the suite; recorded in the spec when done, like every configurator):
run against this machine with a mix of npm/uvx/bash tools, once online and once with the network
blocked, confirming offline turns `outdated` into `unknown` and never into `current`.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| The command ships looking broken because two thirds of the catalog is `unsupported` | **High** — measured (F-5) | FR-10's footer states the count and names the tools; OQ-1 is the real fix and is the next spec, so v1's honesty is a stopgap with an owner |
| Moving the collector subtly changes `update`'s picker | Medium | M2 is a pure move with a parity test; do it as its own commit so a regression bisects to it |
| Fixing the uv race changes which tools read as current | Low | The race only duplicated work, never altered results; parity test covers it |
| `!=` comparison produces false `outdated` for prereleases / held packages (F-4) | Medium | Documented in the spec and README; not fixed in v1 (OQ-2) |
| Network-blocked environments make every checkable tool `unknown` | Medium | Intended — that is the correct answer. The summary line makes it obvious |
| `-vv` probe logging from 8 threads interleaves on stderr | Low | stderr only, one line per call; if unreadable, serialise log writes in `verbose.py` rather than change the pool |

## Out of scope for this plan

OQ-1 (checkable `bash` tools) and OQ-2 (version ordering) were resolved as deferred follow-ups
(2026-10-10) and get their own spec directories; OQ-3 (`--exit-code`) waits for a concrete request. `profile` (M2/M3 of the roadmap) consumes this command's collector
and its five-state vocabulary, and should cite SD-2/SD-3 rather than re-deriving them.
