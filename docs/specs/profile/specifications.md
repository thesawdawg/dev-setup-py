# Specification: `devstuff profile` (snapshot, diff)

**Date:** 2026-10-10
**Status:** Draft — open questions await a decision (§5)
**Authors:** Sawyer + Claude
**Roadmap:** [M2](../../ROADMAP.md) — `snapshot` and `diff`. `apply` is M3 and gets its own
requirements before it starts; §6 records what is already known about it.
**Builds on:** [`outdated/`](../outdated/) — reuses its honesty rule (SD-2/SD-3) and its
collector conventions.

---

## 1. Problem Statement & Goals

devstuff can tell you about **one tool** (`list`, `outdated`) but nothing about **a machine**:

- **There is no portable description of "the tools I have."** `devstuff catalog export` writes
  the tool *definitions* a user added; it says nothing about which tools are installed. Two
  machines with the same catalog can have entirely different installs, and nothing records it.
- **There is no way to ask "is this machine in the state I want?"** `list` prints this
  machine's state for a human to eyeball; it cannot be compared against anything, committed to
  git, or gated on in CI.
- **There is no starting point for reproducing a machine** — the M3 `apply` goal — until the
  file format and the comparison exist and have been used for a while.

**Goal:** a small, hand-editable, git-friendly YAML file listing which catalog tools a machine
should have (optionally at which version), with `snapshot` to write it from a machine and `diff`
to compare a machine against it.

**Non-goals** (reasons in `stack-decisions.md`):
- **No `apply`** in this milestone. Nothing here installs, updates or removes anything.
- **No tool definitions in a profile.** It names catalog keys only; it cannot carry an install
  script. A tool that exists only in one machine's user catalog is reported, not smuggled across.
- **No configurator state** (starship, lazygit, bat…). Several configurators do not round-trip
  (pre-commit and commitizen explicitly do not); capturing them is a separate spec.
- **No version ordering** — equality only, the same decision as `outdated` OQ-2.
- **No remote registry, sync, or named-profile directory** (OQ-5). A profile is a file at a path.
- **Not configuration management.** No conditionals, templating, inventory or per-host variables.

---

## 2. Functional Requirements

### The file format

- **FR-1** A profile is a YAML mapping shaped like the catalog's:

  ```yaml
  version: 1
  tools:
    uv: {}
    lazygit:
      version: 0.45.0
  ```

  `version` is the *file format* version and must be `1`. `tools` maps a catalog key to an entry.
  The only entry field in v1 is `version` (a string): the version this tool should be at.
- **FR-2** An entry with no fields — `uv: {}` or a bare `uv:` (which YAML parses as null) — means
  "present, any version".
- **FR-3** Loading is **strict** and fails with a `ProfileError` naming the file and the key, in
  the same spirit as `catalog.py`: an unknown top-level field, an unknown entry field, a
  non-mapping `tools`, a non-mapping entry, or an unsupported file version.
- **FR-4** An entry's `version` that does not load as a **string** is an error, not coerced. YAML
  turns `version: 1.10` into the float `1.1` and `0.40` into `0.4`, and the original text cannot
  be recovered (§4, F-5). The message says to quote the value.
- **FR-5** A **duplicate key** under `tools` is an error. PyYAML silently keeps the last one and
  drops the first (§4, F-5), which in a file that says what should be installed is a quiet way to
  lose an entry.
- **FR-6** Whether a key exists in *this* machine's catalog is **not** checked at load. A profile
  written on a machine with custom tools must still load on one without them; the key is reported
  by `diff` (FR-17) instead.

### `devstuff profile snapshot`

- **FR-7** `devstuff profile snapshot [-o PATH] [--versions] [--force]` writes a profile for every
  **installed** tool in the effective catalog (bundled + user). With no `-o`, to stdout; with
  `-o PATH`, to that file and nothing on stdout.
- **FR-8** `-o PATH` refuses to overwrite an existing file unless `--force` is given. A profile is
  the file people hand-edit and keep; the cheap mistake is re-running snapshot over it.
- **FR-9** Output is **deterministic**: tools sorted by key, no timestamp, no hostname, no
  username, no paths. Two snapshots of an unchanged machine are byte-identical, so a profile kept
  in git shows only real changes — and one shared with someone else leaks nothing about its origin.
- **FR-10** By default every entry is unpinned (FR-2). `--versions` additionally records `version`
  for each tool whose **type can honour a pin** (`npm`, `pip`, `uvx`, `apt` with a single package —
  §4, F-2) and whose installed version can be read. Tools of any other type never get a version:
  `update` cannot install one, so a recorded version could never be acted on and would show as
  drift each time the tool updated itself.
- **FR-11** A version is read by a **local-only, per-type reader** (`npm list -g`, `uv tool list`,
  `dpkg-query`) — never from `get_version()`'s text, which is free-form (`bat 0.26.1 (979ba22)`,
  `gh version 2.102.0 (2026-09-30)`; §4, F-1), and never via `check_for_update`, which also
  queries the network (§4, F-3). So `snapshot` works offline.
- **FR-12** If `--versions` is given and a pinnable tool's version cannot be read, the entry is
  written **without** a version and a warning on stderr names the tool and says why. Silently
  dropping it would be indistinguishable from "unpinned on purpose".
- **FR-13** Tools that come from the user's own catalog (not the bundled one) are included, and a
  single stderr line says how many there are and that another machine needs
  `devstuff catalog export` / `import` to know them (OQ-3).
- **FR-14** With no `-o`, **stdout is exactly the YAML** — nothing else, at any `-v` level. A
  profile piped into a file or another tool must parse (same invariant as `outdated --json`).

### `devstuff profile diff`

- **FR-15** `devstuff profile diff PROFILE [--all] [--json] [--exit-code] [--ignore-extras]`
  compares this machine to a profile. Read-only: it installs, updates and writes nothing.
- **FR-16** Every key lands in exactly one state:

  | State | Meaning |
  |---|---|
  | `ok` | In the profile and installed; and, if pinned, at the pinned version. |
  | `missing` | In the profile and the catalog, but not installed. |
  | `drift` | Pinned and installed, but the installed version differs from the pin. |
  | `unverifiable` | Pinned and installed, but the installed version cannot be read. |
  | `unpinnable` | Pinned, but this key's install type cannot honour a pin — the pin is ignored. |
  | `unknown-key` | In the profile but not in this machine's effective catalog. |
  | `extra` | Installed and in the catalog, but not in the profile. |

- **FR-17** `unverifiable`, `unpinnable` and `unknown-key` are **never** `ok` and are never counted
  as `ok`; every state is counted separately and the counts sum to the number of keys considered.
  This is the rule `outdated` exists to enforce (its SD-2/SD-3), applied again: "could not check"
  must not read as "matches".
- **FR-18** Versions are compared by **string equality** — no ordering, no normalisation. `0.45`
  and `0.45.0` differ. (Same stance as `outdated` OQ-2; a half-right comparator is worse than a
  documented `==`.)
- **FR-19** By default only differences are shown; `ok` rows are summarised in the closing line and
  listed with `--all`. `extra` rows are shown as a single collapsed footer line naming the tools,
  because a deliberately small profile will have many.
- **FR-20** The closing summary lists every non-zero state count, e.g.
  `2 missing · 1 drift · 1 unknown-key · 31 ok`.
- **FR-21** `--json` writes one array to stdout and nothing else: objects with `key`, `state`,
  `type` (null for `unknown-key`), `pinned`, `installed` (both null when absent) and `note`. It
  includes `ok` and `extra` rows, always — filtering is a display concern.
- **FR-22** **Exit status.** By default `0` whenever the comparison ran, whatever it found.
  `--exit-code` makes it `1` if any key is in a state other than `ok`; `extra` counts unless
  `--ignore-extras` is given (OQ-2, OQ-4). An unreadable or invalid profile is `2`, so `1` stays
  unambiguous as "differs".

### Structure

- **FR-23** `profile` is a Click **group** with `snapshot` and `diff` as subcommands, registered in
  `cli._register_commands` and listed in the `devstuff help` table. `-v`/`-vv` apply to it
  centrally like every other command. Running `devstuff profile` alone prints help.
- **FR-24** Loading, validation, dumping and the diff classifier live in `dev_setup/profile.py` as
  pure functions with no UI and no I/O beyond reading the file, so they are testable without a
  terminal — the arrangement `updates.py` set.
- **FR-25** Local version reading is a new `GenericTool.installed_version()` that dispatches on
  install type through a table, like `_CHECKERS` and `_UPDATE_CHECKERS`. It returns `""` when the
  type has no reader or the read fails, and never raises.
- **FR-26** Whether a type can honour a pin is an **explicit predicate**, `supports_pin(tool)`, and
  a test asserts it agrees with what `update(version=…)` really does for every install type
  (§4, F-2), so the two cannot drift apart.

---

## 3. Non-Functional Requirements

- **NFR-1** **stdout carries data only** for `snapshot` (without `-o`) and `diff --json`, at every
  verbosity level. Verbose lines go to stderr.
- **NFR-2** **`snapshot` makes no network call.** Measured parts on this machine: all 35 catalog
  `is_installed()` checks took 0.43 s, `uv tool list` 0.01 s, one `npm list -g` 0.52 s.
- **NFR-3** **No new runtime dependency.** PyYAML is already required by the catalog loader.
- **NFR-4** **Round trip.** `load(dump(p)) == p` for every profile `snapshot` can produce, including
  versions that look numeric (`'0.45'`, `'1.10'`); and `diff` of a fresh snapshot against the
  machine that produced it reports no differences.

---

## 4. Findings from measurement

Per the project convention (*measure the tool, don't recall it*). Date: 2026-10-10.

| # | Finding | Consequence |
|---|---------|-------------|
| F-1 | `get_version()` is the first line of `--version` output, and nothing more structured: `'bat 0.26.1 (979ba22)'`, `'codex-cli 0.162.0'`, `'gh version 2.102.0 (2026-09-30)'`, `'devin 3000.11.3 (9c803229faa4)'`, `'uv 0.12.23 (x86_64-unknown-linux-gnu)'`, and `''` for `nerd-font`. Only 4 of 13 installed tools (`commitizen`, `ipython`, `nvm`, `pi`) return a bare version. | A snapshot cannot record these as pins (FR-11, SD-3). |
| F-2 | Only four install types can honour a pin on `update`: `npm` (`pkg@ver`), `pip`/`uvx` (`pkg==ver`), `apt` (`pkg=ver`, single package only). `git` (`pull`), `script` and `bash` raise "Version pinning is not supported". | FR-10, FR-26. A pin on a `git`/`script`/`bash` key is `unpinnable`. |
| F-3 | A clean installed version exists only inside the `_check_update_*` functions, fused with a network lookup of the latest. Run offline, `outdated` took ~11 s waiting on timeouts. | `snapshot` needs local-only readers, not the checkers (FR-11, FR-25). The local reads already exist inside them (`_npm_installed_version`, `_uv_tool_versions`, the `dpkg-query` call) and can be factored out. |
| F-4 | `install()` takes **no version** (`Tool.install(self)`); `update(version=…)` requires the tool to already be installed. | Not an M2 concern, but it shapes M3: applying a pin is install-then-update (§6). |
| F-5 | **YAML corrupts version-shaped scalars.** `version: 1.10` → float `1.1`; `0.40` → `0.4`; `2` → int; `2026-09-30` → a date object. A duplicate key under `tools` is silently dropped (last wins). `yaml.safe_dump` *does* quote a string that would otherwise reload as a number (`'1.10'`). | FR-4 and FR-5 reject rather than coerce; the emit side is safe, so NFR-4's round trip holds. |
| F-6 | `registry.missing_requires(tool)` already returns the transitive missing-dependency closure, deepest first. | Reusable for M3 ordering; no second resolver (§6). |

---

## 5. Open questions

| # | Question | Recommendation | Status |
|---|----------|----------------|--------|
| OQ-1 | Should `snapshot` record versions by default, or only with `--versions`? | **Keys only by default.** Most people want "the same tools", and a pinned-by-default profile goes stale the day a tool updates, then reports drift nobody intended. `--versions` is one flag away when reproducibility is the point. | Open |
| OQ-2 | Do `extra` tools count as a difference for `--exit-code`? | **Yes**, with `--ignore-extras` to relax. "Is this machine exactly what the profile says" is the question a gate asks; a partial profile is the case `--ignore-extras` is for. | Open |
| OQ-3 | Include tools from the user's own catalog in a snapshot? | **Yes**, plus the stderr note in FR-13. Omitting them would make the snapshot silently incomplete; embedding their definitions would break the "keys only" rule (SD-2). | Open |
| OQ-4 | Ship `--exit-code` in v1? | **Yes.** `outdated` deferred it for lack of a concrete user; here drift-gating is the headline use case. Off by default, so FR-22's default holds. | Open |
| OQ-5 | A named-profile directory (`~/.config/devstuff/profiles/<name>.yaml`) so `diff work` works? | **No in v1** — paths only. It adds a lookup order and a place for files to hide; add it if typing paths becomes the complaint. | Open |
| OQ-6 | Should `apt` entries with several packages be pinnable? | **No** — `unpinnable`. `update` supports a pin for a single package only (F-2), and a profile entry has one `version`. | Open |

---

## 6. Known constraints for M3 (`apply`) — not requirements yet

Recorded now because they were found now and will not be cheaper to rediscover. `apply` gets its
own numbered requirements, and a decision on each of these, before it starts.

- **Pinned apply is install-then-update** (F-4): install the latest, then `update(version=…)`. For a
  pin that is older than latest that is two installs, and for `apt` a downgrade. Alternatives —
  teaching the `npm`/`uvx`/`apt` installers to take a version — are a decision for M3.
- **Ordering** comes from `registry.missing_requires` (F-6), not a second resolver.
- **Pins on `git`/`script`/`bash` keys cannot be honoured** and `apply` must say so rather than
  silently installing latest.
- **Failure policy:** continue past a failed tool, skip its dependents with a stated reason, and
  summarise — roadmap recommendation, to be confirmed.
- **Bundled bundles** (`@backend`…) are profile files shipped in the package; they need the same
  every-key-exists-in-the-bundled-catalog test the CI matrix has.
- **Plan before action** — reuse the preview used by `install`/`update`.
