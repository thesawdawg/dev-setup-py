# Specification: `devstuff outdated`

**Date:** 2026-10-10
**Status:** Approved (2026-10-10) — open questions resolved, implementation not started
**Authors:** Sawyer + Claude
**Roadmap:** [M1](../../ROADMAP.md) — the foundation `profile diff` (M2) is built on.

---

## 1. Problem Statement & Goals

`devstuff update` is the only place that knows whether an installed tool is behind, and it
only tells you as a side effect of opening an interactive picker that ends in a mutation:

- **There is no read-only answer.** You cannot ask "what is behind?" from a script, a cron job
  or a CI step. `update` with no arguments opens a questionary checkbox, which hangs in a
  non-TTY shell (see CLAUDE.md, "Commands").
- **The gathered answer is private.** `_collect_update_candidates()` in `commands/update_cmd.py`
  already probes every installed tool concurrently and returns `(tool, UpdateStatus)` pairs, but
  nothing outside that one file can call it.
- **"Unknown" is the common case, and the picker undersells it.** On the bundled catalog 23 of
  35 tools (66%) are `bash` installers, which have no update checker at all. The picker labels
  them `unknown — reinstall to check`, which reads as a hint rather than as "devstuff cannot
  see this tool". A report that is mostly blind needs to say so plainly.

**Goal:** a read-only, non-interactive command that reports installed vs. latest per tool,
shares one probing path with `update`, and never presents "could not check" as "up to date".

**Non-goals** (each one is a decision, with reasons in `stack-decisions.md`):
- No mutation of any kind. `outdated` never offers to update; that is `devstuff update`.
- No new update checkers for `script`/`bash` tools in v1 — see OQ-1.
- No version *ordering*. v1 reports "different from the latest", not "older than the latest" — see OQ-2.
- No persistent cache and no background refresh.
- No uninstalled tools, no checking for new *catalog* entries.
- No notification, cron or shell-prompt integration; `--json` is the integration surface.

---

## 2. Functional Requirements

### Command surface

- **FR-1** `devstuff outdated [KEY…]`. With no keys, every **installed** tool is checked. With
  keys, exactly those tools are checked. An unknown key prints an error and exits 1, matching
  `update`. A known key that is not installed yields a `not-installed` row (FR-5), not an error.
- **FR-2** Never prompts, never opens a questionary widget, and works with stdin and stdout
  redirected.
- **FR-3** Read-only. It must not call `install`, `remove`, `update`, `configure`, or write to
  the catalog or any config directory. (Test: patch each to raise.)
- **FR-4** Two display flags: `--updates-only` hides every row that is not `outdated`;
  `--all` shows the `unsupported` rows that are otherwise collapsed (FR-10).

### The five states

- **FR-5** Every checked tool lands in exactly one state:

  | State | Meaning | Source |
  |---|---|---|
  | `outdated` | a different version is available | `UpdateStatus.available is True` |
  | `current` | the checker found nothing newer | `available is False` |
  | `unknown` | a checker exists but could not answer | `available is None`, type has a checker |
  | `unsupported` | no checker exists for this install type | type absent from `_UPDATE_CHECKERS` |
  | `not-installed` | named explicitly but not installed | `is_installed()` is False |

- **FR-6** `unsupported` is decided from the tool's `install_type` against the checker table,
  **not** inferred from an empty `UpdateStatus`. An empty status is what a *failed* probe also
  returns, so inferring would merge "cannot be checked" with "the check failed" — two states
  whose remedies differ (nothing to do vs. check your network).
- **FR-7** `unknown` and `unsupported` are never rendered with the same glyph, colour or wording
  as `current`, and are never counted in a "current" total. This is the requirement the whole
  command exists to protect; it has a test per state (FR-20).
- **FR-8** `current` means "the checker found no newer version", which for `uvx` and `pip`
  tools does not include the latest version number (see §4, finding F-3). The `Latest` column is
  blank for those rows rather than echoing `Installed`.

### Output

- **FR-9** Table columns: `Package`, `Type`, `Installed`, `Latest`, `Status`. Rows sort
  `outdated`, `unknown`, `current`, `not-installed`; alphabetical by key within a state.
- **FR-10** `unsupported` rows are collapsed by default into one dim footer line
  (`23 tools can't be checked (bash installers): bat, gh, …`) and shown as rows with `--all`.
  They are the majority on the bundled catalog and would otherwise bury the answer.
- **FR-11** A one-line summary closes the output, with all five counts that are non-zero:
  `3 outdated · 6 current · 2 unknown · 23 unsupported`. Counts always sum to the number of
  tools checked.
- **FR-12** `--json` writes a single JSON array to stdout and nothing else:
  `{"key", "type", "state", "installed", "latest", "note"}`, with `null` for absent versions and
  an empty string for an absent note (FR-23). `state`
  uses the five names in FR-5. Everything else (banner, spinner, hints) is suppressed or goes
  to stderr — see NFR-1.
- **FR-13** The banner and spinner are shown only when stdout is a TTY and `--json` is absent.

### Exit status

- **FR-14** Exit 0 whenever the lookup was performed, **regardless of what it found**. Non-zero
  is reserved for "could not perform the lookup" (unknown key, catalog failure). This is the
  `run_cmd` rule from CLAUDE.md: a correct answer must not arrive under a red failure banner.

### Probing

- **FR-15** Version information comes from `GenericTool.check_for_update()` and nothing else.
  The command layer adds no subprocess call of its own; anything it did need would go through
  `generic._probe`, per the verbosity rules.
- **FR-16** Probes run concurrently on a bounded pool (8 workers, as `list` and `update` use).
  One tool raising or timing out yields an `unknown` row for that tool and never aborts the run.
- **FR-17** The gathering code is shared. `update`'s interactive picker and `outdated` call
  **one** function that lives outside `update_cmd.py`; `update`'s picker output is unchanged by
  the move (regression test, FR-21).
- **FR-18** Progress: a spinner while probing on a TTY, replaced by a plain logged line at `-v`
  (`verbose.step`), as for every other command.
- **FR-19** Redundant probes are removed: one run invokes `uv tool list` at most once and
  `uv tool list --outdated` at most once, however many `uv` tools are installed (see NFR-2).

- **FR-23** `UpdateStatus` gains one optional field, `note: str = ""`, a short human reason
  (`"not installed via uv tool"`, `"registry unreachable"`). Checkers set it only where they
  actually know why they could not answer; it is never invented by the command layer. It is
  shown dimmed after the status text and carried in `--json`. A checker that does not set it
  behaves exactly as before — the field defaults to empty, so no existing call site changes.
  (Resolves OQ-4; motivated by finding F-2.)

### Tests

- **FR-20** One test per state asserting its rendering differs from `current`'s, including a
  real `bash`-type tool landing in `unsupported`.
- **FR-21** The shared collector returns what `update`'s picker consumed before the move.
- **FR-22** `outdated --json` at `-vv` parses as JSON (stdout purity, NFR-1).

---

## 3. Non-Functional Requirements

- **NFR-1** **stdout carries data only.** Under `--json`, even at `-vv`, stdout is exactly one
  JSON document. Same invariant, same reason, as `verbose-mode` SD-3: a consumer is parsing it.
- **NFR-2** **Probe cost is bounded by the number of distinct questions, not tools.** Measured
  baseline in §4 (F-1): six `uv tool list` calls for three installed uv tools, where two
  suffice. After this work, the count is independent of how many uv tools are installed.
- **NFR-3** **Latency.** The measured baseline is 2.6 s for 13 installed tools on a warm
  network. No target is set beyond "not regress"; per-probe timeouts (10–20 s) bound the worst
  case, and there is deliberately no global timeout in v1 (OQ-5).
- **NFR-4** **No new runtime dependency.** Rich is already present; JSON is stdlib.

---

## 4. Findings from measurement

Per the project convention (*measure the tool, don't recall it*), the existing probe path was
run before this spec was written. Date: 2026-10-10; 13 installed tools.

| # | Finding | Consequence |
|---|---------|-------------|
| F-1 | `uv tool list` ran **6** times for 3 installed uv tools; 4 are needed. `_uv_outdated_map` is `@lru_cache(maxsize=1)`, but `lru_cache` does not stop *concurrent* first callers from each computing the value, so all three worker threads ran `uv tool list --outdated`. `_uv_tool_current_version` has no cache at all, so it runs once per tool. | FR-19 / NFR-2. The cache needs a lock (or the collector must prime it before fanning out) and the per-tool version lookup must read from one shared `uv tool list` parse. |
| F-2 | `commitizen` is reported installed (`cz` on `PATH`) but is not a `uv tool` — here it comes from the project venv under `uv run`. The uvx checker asks `uv tool list`, finds nothing, and returns an empty status: `unknown`. | `is_installed()` ("is the command present?") and the update checker ("does this mechanism manage it?") ask different questions and can disagree. `unknown` is the right answer; OQ-4 asks whether it should say *why*. |
| F-3 | For `uvx` tools the checker only knows the *absence* of a tool from `uv tool list --outdated`. `hey-dave` and `ipython` came back `available=False` with `latest=''`. | `current` for uvx is "not listed as outdated", so `Latest` is blank (FR-8). Also: `uv … --outdated` does not consider prereleases, so `hey-dave 0.2.0a1` can read as current while a newer alpha exists. Documented, not fixed. |
| F-4 | `npm` and `apt` checkers decide `available` by `current != latest` — inequality, not ordering. | A locally newer version (a prerelease, a held or PPA package) reads as `outdated`. OQ-2. |
| F-5 | 23 of 35 bundled tools are `bash`; none has a checker. | The command is blind to two thirds of the catalog. FR-7 and FR-10 exist because of this; OQ-1 is the real fix. |
| F-6 | `check_for_update` exists on `GenericTool` but not on the `Tool` ABC; `update_cmd` suppresses the type error with `# type: ignore[attr-defined]`. | The shared collector either takes `GenericTool` or the ABC gains a default. Decided in SD-4. |

---

## 5. Open questions (all resolved)

| # | Question | Recommendation | Status |
|---|----------|----------------|--------|
| OQ-1 | Should `bash`/`script` tools be checkable — e.g. an optional catalog field naming where the latest version lives (`latest_from: github:owner/repo`)? | **Yes, but as its own spec after v1 ships.** It is a schema change (`SUPPORTED_FIELDS`, validation, README, schema docs) and decides how `profile diff` sees two thirds of the catalog, so it deserves its own design. v1 ships honest unknowns. | **Resolved 2026-10-10: yes, as its own spec after v1 ships.** v1 keeps honest `unsupported` rows. |
| OQ-2 | Compare versions by ordering instead of inequality? | Not in v1. Version grammars differ per ecosystem (PEP 440, semver, dpkg); a half-right comparator is worse than a documented `!=`. Revisit with OQ-1. | **Resolved 2026-10-10: not in v1.** Inequality stays, documented (F-4); revisit alongside OQ-1. |
| OQ-3 | An opt-in `--exit-code` (exit 1 when anything is outdated), `git diff --exit-code` style, for CI? | Probably yes, as a flag that is off by default so FR-14 stays true. Wait for a concrete request. | **Resolved 2026-10-10: deferred.** Not in v1; FR-14 stays true. Add as an opt-in flag on a concrete request. |
| OQ-4 | Should `UpdateStatus` carry a short `note` (`"not installed via uv tool"`, `"registry unreachable"`) so `unknown` can say why? | Yes — one optional string, default empty, populated only where a checker knows. Without it F-2 is indistinguishable from an offline failure. | **Resolved 2026-10-10: yes.** Specified as FR-23. |
| OQ-5 | A global timeout on the whole run? | No for v1: per-probe timeouts already bound it, and a global one would turn a slow-but-correct tool into `unknown`. | **Resolved 2026-10-10: no global timeout in v1.** |
