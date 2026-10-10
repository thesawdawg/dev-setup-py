# Development plan: `devstuff profile` (snapshot, diff)

**Date:** 2026-10-10
**Status:** Approved (2026-10-10) — P1 done

---

## Milestones

Each milestone is one commit. Ordering puts the pure, no-I/O pieces first so the rules that matter
are tested before any command exists — the order that worked for `outdated`.

| # | Milestone | Satisfies | State |
|---|-----------|-----------|-------|
| P1 | `dev_setup/profile.py`: `Profile` model, strict loader (duplicate-key detecting), deterministic dumper | FR-1–6, FR-9, FR-24, NFR-4 | **Done** 2026-10-10 |
| P2 | `GenericTool.installed_version()` (local readers factored out of the checkers), `supports_pin`, and the test tying it to `update(version=…)` | FR-10–12, FR-25–26, SD-3/4/10 | Not started |
| P3 | `devstuff profile snapshot` (group + command, `-o`, `--versions`, `--force`, stderr notes), registered in the CLI and help table | FR-7–8, 13–14, 23, NFR-1/2 | Not started |
| P4 | Diff classifier — pure function over (profile, installed state, catalog) → seven states, counts, ordering | FR-16–18, 20 | Not started |
| P5 | `devstuff profile diff` (table, footer, `--all`, `--json`, `--exit-code`, `--ignore-extras`, exit 2 on bad profile) | FR-15, 19, 21–22, NFR-1 | Not started |
| P6 | README command reference, CLAUDE.md rules, roadmap + specs index | — | Not started |

P2 is the only milestone that changes existing behaviour (it factors local reads out of
`_check_update_npm` / `_uv_tool_versions` / `_check_update_apt`). It lands before any command uses
it, and its parity test pins that `check_for_update` returns what it did before.

## Testing strategy

Unit-only; no network, no real installs — default suite, ~0.3 s each file.

- **Loader, exhaustively (P1).** One parametrized test over every rejection in FR-3–5: unknown
  top-level field, unknown entry field, `tools` not a mapping, entry not a mapping, file version
  `2`, a duplicate key, and each of `1.10`, `0.40`, `2`, `2026-09-30` as an unquoted `version`
  (the F-5 inputs, verbatim). Each asserts the error names the file and the key. And the accepted
  spellings: `uv: {}`, bare `uv:`, `version: '1.10'`.
- **Round trip (P1, NFR-4).** `load(dump(p)) == p` over versions that look numeric — `'0.45'`,
  `'1.10'`, `'2'` — since that is exactly where a hand-rolled emitter would fail and `safe_dump`
  must be proven to hold.
- **Determinism (P1/P3, FR-9).** Two snapshots of the same fake machine are byte-identical, and the
  output contains no hostname, username, home path or digits that look like a date.
- **Pinnability matches reality (P2, FR-26).** For every install type, build a minimal tool, call
  `update(version="1.2.3")` with the installers stubbed out, and assert it raises *iff*
  `supports_pin` is False. This must fail if someone teaches `git` to pin without updating the
  predicate — verified by temporarily breaking one side, like `outdated`'s renderer mutation check.
- **Local readers never touch the network (P2, NFR-2).** Stub `_probe` to record argv and assert
  `installed_version()` issues only `npm list`, `uv tool list` or `dpkg-query` and never `npm view`,
  `uv tool list --outdated`, `apt-cache policy` or `git ls-remote`.
- **Readers never raise (P2, FR-25).** A stub raising `RuntimeError` or timing out → `""`.
- **Snapshot (P3).** Default has no `version` keys; `--versions` adds them only for pinnable types;
  an unreadable pinnable tool yields an entry *without* a version plus a stderr warning naming it
  (FR-12); `-o` writes the file and leaves stdout empty; an existing `-o` target is refused without
  `--force`; custom-catalog tools are included and counted on stderr (FR-13).
- **Stdout purity (P3/P5, NFR-1).** `snapshot` and `diff --json` at `-vv` with a fake tool whose
  probe logs: stdout parses as YAML/JSON, stderr is non-empty so the test is not vacuous.
- **Classifier, exhaustively (P4).** One row per state, plus the pairs that must stay apart: a
  pinned `bash` key (`unpinnable`) vs a pinned `npm` key whose version cannot be read
  (`unverifiable`) vs a key the catalog lacks (`unknown-key`). Counts sum to keys considered, and
  none of the three folds into `ok` (FR-17). A rendering test breaks the renderer so `unverifiable`
  shows as `ok` and asserts the test fails — the guard that `outdated` needed and had.
- **Exit status (P5, FR-22).** Default `0` with differences present; `--exit-code` `1` on a
  difference and `0` on a clean match; `extra` flips it unless `--ignore-extras`; a malformed
  profile and a missing file are `2` both with and without `--exit-code`.
- **Read-only (P5, FR-15).** Fakes whose `install`/`remove`/`update` raise; the command completes.
- **Self-diff (NFR-4).** On a fake machine, `diff(snapshot())` reports no differences, and with
  `--versions` too.

**Live verification** (not in the suite; recorded here when done): on a real machine, `snapshot
--versions` then `diff` reports clean; then pin one tool to a wrong version, uninstall nothing, add
a bogus key, and check the report shows exactly `drift` and `unknown-key` and exits `1` under
`--exit-code`. Repeat `snapshot` with the network blocked — it must succeed, since it asks the
network nothing (NFR-2). `outdated`'s equivalent step found a real bug (its F-7) that the unit tests
could not, so this step is not optional.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| The format hardens before `apply` has been tried (roadmap risk) | Medium | Mark the file format provisional in the README until M3 lands; `version: 1` stays reserved for the stabilised shape, and a later breaking change bumps it |
| Factoring local reads out of the update checkers (P2) subtly changes `outdated`/`update` | Medium | A parity test over each checker before and after; P2 is its own commit so a regression bisects to it |
| A profile pins a version the registry no longer serves, and `diff` reports `drift` forever | Medium | Intended — drift is what it says. `apply` (M3) decides what to do about an unobtainable pin |
| `unknown-key` is common because the user catalog differs between machines | High | It is a first-class state with its own wording and a pointer to `catalog export`/`import`, not an error |
| `--exit-code` gets wired into CI by people who then set `--ignore-extras` everywhere | Low | Not a risk to correctness; the flag exists for it |
| `apt` pins are flaky (a version string that exists only on one release) | Medium | Reported as `drift`/`unverifiable` truthfully; `apply` is where the cost lands, and it is deferred |

## Out of scope for this plan

`apply`, bundled bundles, and configurator capture. `apply` needs its own requirements and a
decision on each constraint in `specifications.md` §6. Configurator capture is a separate spec
and does not block M3.
