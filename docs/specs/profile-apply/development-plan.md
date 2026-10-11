# Development plan: `devstuff profile apply`

**Date:** 2026-10-10
**Status:** Complete — A1 done 2026-10-10, A2–A6 done 2026-10-11 (the container test's first run is the one open item)

---

## Milestones

One commit each, pure pieces first so the rules that matter are tested before anything can touch a
machine — the order that worked for `outdated` and `profile`.

| # | Milestone | Satisfies | State |
|---|-----------|-----------|-------|
| A1 | **Planner** — pure `build_plan(rows, tools)`: row → action table, dependency closure across the plan with de-duplication, ordering, the skip-because-dependency-failed propagation as a pure function over results | FR-2, 3, 9, 22 | **Done** 2026-10-10 |
| A2 | **Step runner** — `apply_exec.perform` (install / pinned install in one call, failure as a result, never a wizard offer); `generic.forget_probes()` that clears every memoised probe. `install_cmd` untouched (SD-10) | FR-6, 7, 10, NFR-5 | **Done** 2026-10-11 |
| A3 | **Executor** — runs a `Plan` serially under the failure policy, handles `^C` between steps, returns per-step results | FR-8, 9, 11, 19 | **Done** 2026-10-11 |
| A4 | **`devstuff profile apply`** — plan table, `--dry-run`, `--yes`, TTY refusal, `--missing-only`, final re-measurement, summary, exit status, `--json` | FR-4, 5, 12–18, 23, NFR-1–4 | **Done** 2026-10-11 |
| A5 | **Bundles** — `@name` resolution, the shipped files, the two data tests; also accepted by `diff` | FR-20, 21 | **Done** 2026-10-11 |
| A6 | **Docs + integration** — README, CLAUDE.md rules, roadmap, specs index; a Docker integration test that applies a profile to an empty container and diffs clean | NFR-4 | **Done** 2026-10-11 — docs written; the container test is written and its `uv` + pinned-`commitizen` half was run against the real installers (isolated `HOME`); the `yq` half and the first run in the CI image are pending |

Nothing in this plan changes `devstuff install`: A2 was first written as a refactor of `install_cmd`
and became a new module once measured (SD-10). The only edit to existing code is additive —
`forget_probes()` and a registry of memoised probes in `generic.py`, which changes no behaviour.

## Testing strategy

Unit tests use fake tools and a fake runner — no real installs, default suite. Real behaviour is
checked separately (live below), because a unit test that fakes success is exactly how the uv
probe's ignored exit code went unnoticed in `outdated`.

- **Planner decision table (A1).** Exhaustive, like `diff`'s 72-case grid: every `diff` state ×
  pinned or not × pinnable or not × has-missing-dependency or not. Each asserts the action and the
  reason text, and that no combination plans an action on a `unverifiable`/`unpinnable`/
  `unknown-key`/`extra` row.
- **Ordering (A1).** Shared dependency across two requested tools appears once and before both; a
  depth-3 chain comes out deepest-first; a dependency the profile names *and* another tool needs
  appears once, not labelled as an extra. A property test over every tool in the bundled catalog:
  for any subset requested, every step's `requires` precede it.
- **Failure propagation (A1/A3).** A fails ⇒ everything requiring A is skipped with "needs A, which
  failed"; a *sibling* is still attempted; a dependency that was *skipped* (not failed) also skips
  its dependents, transitively.
- **Pinned install is one call (A2).** A spy asserts `update(version=…)` is called exactly once and
  `install()` never, for a missing pinned tool; and the reverse for an unpinned one.
- **Stale cache (A2/A4).** A fake whose `installed_version()` reads a `_once`-style memo: without
  the reset the final comparison says `unverifiable`, with it `ok`. Written *first* and shown to
  fail on the unfixed code.
- **No wizard prompt (A2).** The core never calls `offer_after_install`; `devstuff install` still
  does. A trap that raises if invoked from `apply`.
- **Command (A4).** Non-TTY without `--yes` ⇒ exit 2 and *nothing run* (spy asserts zero installer
  calls); `--dry-run` ⇒ exit 0 and zero calls; `--json` stdout parses and stderr carries the rest;
  exit 1 when a step failed, when an entry is unactionable, and 0 only when the final diff is clean;
  a bad profile ⇒ exit 2 before any probe.
- **Idempotence (A4, NFR-4).** Apply, then plan again against the fake machine the first run
  mutated: the plan is empty.
- **Bundles (A5).** Every shipped bundle loads; every key it names is in the bundled catalog; and
  the reverse direction — every file in the bundle directory is reachable by name — so a bundle can't
  be added and forgotten.

### Mutation checking

As with the previous milestones: after the tests pass, break the code on purpose and confirm a test
fails. The mutations worth making here: apply to an `extra` row; drop the dependency de-duplication;
let a skipped dependency's dependents run; treat a return-without-raise as final success (skip the
re-measurement); forget the cache reset; remove the TTY refusal; call `install()` then `update()`.
A survivor means a test is missing, not that the mutation was unfair.

### Live verification (not in CI)

1. **Isolated uv end to end.** With `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` pointed at a scratch directory,
   apply a profile pinning a small package at its *older* release (as measured in F-3), then run
   `profile diff` on the same file: clean. Apply again: empty plan.
2. **A deliberate failure.** *(Run on 2026-10-11 through the command, in an isolated `HOME`, catalog and `UV_TOOL_DIR`: the real pinned install succeeded, an impossible pin failed with uv's message, its dependent was skipped unattempted, the unknown key was reported, and the exit status was 1. That run is also what found F-8.)* A profile naming a package version that doesn't exist: the step fails,
   dependents are skipped, siblings proceed, exit status 1, and `diff` agrees with the summary.
3. **Network blocked.** Planning and `--dry-run` work offline; only the install steps fail, with
   their own error.
4. **Docker (A6).** *(Written as `tests/integration/test_profile_apply.py` and `make run-apply-tests`. Docker isn't available where it was written, so it was validated by running its `uv` + pinned `commitizen` half with the real installers in an isolated `HOME` and a `PATH` with no `uv`: the plan ordered `uv` first, the pinned install landed at the pinned version (checked with `uv tool list`), `diff` agreed, and a second plan was empty. The `yq` half installs with `sudo` into `/usr/local/bin` and was deliberately not run on a workstation. First container run pending.)* The existing integration harness: apply a profile in an empty
   container, `diff` clean, apply again and find nothing. This is the roadmap's headline "done when", and the only test that
   exercises `bash`-type installers and `requires` ordering for real. It needs sudo and network and
   is not run by default.

## Risks

| Risk | Mitigation |
|------|------------|
| A `bash` installer exits 0 having installed nothing | The re-measurement (FR-15) judges by `is_installed`, so it is reported rather than believed; the same hazard `devstuff install` already has, now visible |
| `apply`'s runner drifts from what `devstuff install` does | Accepted (SD-10); the shared part is a single call, and the differences (no wizard, no short-circuit) are tested as requirements |
| A long run is interrupted halfway | FR-19: clean stop, honest summary, and re-running `apply` is the resume — NFR-4 makes that safe |
| Applying a profile from someone else | It can only run what *this* machine's catalog defines (`profile` SD-2); the plan is shown and confirmed first, and `--dry-run` previews it without running |
| A sudo prompt mid-run in `--yes` mode | Marked in the plan (FR-5), documented; priming is rejected (OQ-8) |
| Downgrades and non-resolvable apt pins | Reported as failures with apt's own message (FR-8); a pin that can't be honoured is never replaced by "latest" |
| Bundle content is a judgement call, not a fact | Decided with the user at A5 (six bundles, sudo-heavy tools included, no catch-all `@everything`); the test guards only that keys exist, not that a bundle is a good idea |
