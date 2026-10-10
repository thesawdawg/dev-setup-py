# Development plan: `devstuff profile apply`

**Date:** 2026-10-10
**Status:** In progress — A1 done 2026-10-10

---

## Milestones

One commit each, pure pieces first so the rules that matter are tested before anything can touch a
machine — the order that worked for `outdated` and `profile`.

| # | Milestone | Satisfies | State |
|---|-----------|-----------|-------|
| A1 | **Planner** — pure `build_plan(rows, tools)`: row → action table, dependency closure across the plan with de-duplication, ordering, the skip-because-dependency-failed propagation as a pure function over results | FR-2, 3, 9, 22 | **Done** 2026-10-10 |
| A2 | **Non-interactive core** — factor install and pinned-install out of `install_cmd._install_one` (no section, no wizard offer); one function that does a step and returns a result; probe-cache reset helper in `generic.py` | FR-6, 7, 10, 11, NFR-5 | Not started |
| A3 | **Executor** — runs a `Plan` serially under the failure policy, handles `^C` between steps, returns per-step results | FR-8, 9, 11, 19 | Not started |
| A4 | **`devstuff profile apply`** — plan table, `--dry-run`, `--yes`, TTY refusal, `--missing-only`, final re-measurement, summary, exit status, `--json` | FR-4, 5, 12–18, 23, NFR-1–4 | Not started |
| A5 | **Bundles** — `@name` resolution, the shipped files, the two data tests; also accepted by `diff` | FR-20, 21 | Not started |
| A6 | **Docs + integration** — README, CLAUDE.md rules, roadmap, specs index; a Docker integration test that applies a profile to an empty container and diffs clean | NFR-4 | Not started |

A2 is the only milestone that changes existing behaviour (`devstuff install` is refactored to call
the shared core). It lands before any command uses it, with the existing `install` tests as the
parity check, as P2 did for `profile`.

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
2. **A deliberate failure.** A profile naming a package version that doesn't exist: the step fails,
   dependents are skipped, siblings proceed, exit status 1, and `diff` agrees with the summary.
3. **Network blocked.** Planning and `--dry-run` work offline; only the install steps fail, with
   their own error.
4. **Docker (A6).** The existing integration harness: snapshot on this machine, apply in an empty
   container, `diff` clean. This is the roadmap's headline "done when", and the only test that
   exercises `bash`-type installers and `requires` ordering for real. It needs sudo and network and
   is not run by default.

## Risks

| Risk | Mitigation |
|------|------------|
| A `bash` installer exits 0 having installed nothing | The re-measurement (FR-15) judges by `is_installed`, so it is reported rather than believed; the same hazard `devstuff install` already has, now visible |
| Refactoring `install_cmd` regresses `devstuff install` | A2 lands alone with the existing install tests as parity; the wizard offer and "already installed" short-circuit are asserted to stay |
| A long run is interrupted halfway | FR-19: clean stop, honest summary, and re-running `apply` is the resume — NFR-4 makes that safe |
| Applying a profile from someone else | It can only run what *this* machine's catalog defines (`profile` SD-2); the plan is shown and confirmed first, and `--dry-run` previews it without running |
| A sudo prompt mid-run in `--yes` mode | Marked in the plan (FR-5), documented; priming is rejected (OQ-8) |
| Downgrades and non-resolvable apt pins | Reported as failures with apt's own message (FR-8); a pin that can't be honoured is never replaced by "latest" |
| Bundle content is a judgement call, not a fact | Deferred to A5 and decided with the user; the test guards only that keys exist, not that a bundle is a good idea |
