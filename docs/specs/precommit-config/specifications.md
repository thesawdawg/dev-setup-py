# Specification: `devstuff configure pre-commit`

**Date:** 2026-08-01
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

`devstuff install pre-commit` puts `pre-commit` on the PATH. That is where it stops. pre-commit
does nothing at all until two separate things are true: a `.pre-commit-config.yaml` exists, *and*
`pre-commit install` has written the git hooks. Neither is implied by the other, and a repository
with the first but not the second looks configured, validates cleanly, and never runs a single
check.

Writing the file itself means knowing, for every hook you want: which repository publishes it,
what its id is, what tag to pin, and which of its arguments are actually decisions. None of that
is discoverable from `pre-commit --help`; the normal workflow is copying a config out of another
project and hoping it fits. The failure modes are quiet rather than loud:

- `pre-commit install` installs **only** the `pre-commit` hook unless given
  `--install-hook-types`. Add a `commit-msg` hook such as commitizen's, and the config validates,
  the install succeeds, and the hook never fires.
- `pre-commit validate-config` checks the file's *shape*. It never opens a repository, so a
  mistyped hook id passes validation and fails on the user's next commit.
- Two formatters over the same files (ruff-format and black; ruff's import rules and isort) do
  not error. They reformat each other's output on alternate commits, indefinitely.
- `ruff-check` without `--exit-non-zero-on-fix` exits 0 having rewritten files, so the fixes miss
  the commit that triggered them.
- `pre-commit.ci` runs no Docker daemon, so any `language: docker` hook fails there with a
  container error rather than a lint result.

`devstuff configure pre-commit` replaces "copy someone's YAML" with a wizard: choose a preset that
already matches the project, adjust which hooks run, and see what each one will do — and what it
will rewrite — before anything is written.

**Success criteria**

- A user who has never written a pre-commit config gets a working, pinned, project-appropriate
  `.pre-commit-config.yaml` **and installed git hooks** in under two minutes.
- Every repository, rev and hook id shipped in the catalog was read from the real repository, not
  recalled — and the wizard can refresh every pin against the network on demand.
- Nothing is written until the user confirms; an existing config is never lost.
- The generated file is one a human can then hand-edit: commented, grouped, pinned, and using
  pre-commit's own key names.
- Zero new runtime dependencies (PyYAML is already a dependency).

**Non-goals**

- Authoring `repo: local` hooks. Those are project-specific scripts with an `entry`, a `language`
  and a `files` pattern — a different, much larger form, and the thing a user writes by hand once
  they know what they want.
- Round-tripping an existing hand-written config back into wizard state (see SD-5).
- Configuring the *tools* the hooks run. `ruff.toml`, `.eslintrc` and `.yamllint` are their own
  formats; this wizard decides which linters run, not how they are configured.
- Replacing `pre-commit autoupdate`. The wizard calls it rather than reimplementing tag
  resolution.

---

## 2. Functional Requirements

### Catalog and presets

- **FR-1** The hook catalog is data: `REPOS` (repository + pinned rev), `HOOKS` (id, repo, group,
  default args, stages, detection markers) and `PRESETS` in `configure/precommit/model.py`.
  Adding a hook is one `Hook` record, and it reaches the picker, any preset naming it, the
  detector, the emitter and the preview with no other edit.
- **FR-2** Eleven presets are offered: `minimal`, `essentials`, `python` (ruff), `python-strict`,
  `python-black`, `web`, `shell`, `security`, `docs`, `detected`, and `empty`.
- **FR-3** Every preset except `empty` builds on the same language-agnostic essentials, so
  choosing a language preset never means losing file hygiene.
- **FR-4** No shipped preset may contain a self-conflicting pair (two formatters over the same
  files). Asserted by `test_presets_are_free_of_self_conflicts`.
- **FR-5** Hook ids and revs are verified against the real repositories, not recalled. `REPOS`
  revs are what `pre-commit autoupdate` resolves to; ids were read from each repository's own
  `.pre-commit-hooks.yaml` at that rev.

### Project detection

- **FR-6** Before asking anything the wizard reports: git root, whether it is a git repository,
  languages present, an existing `.pre-commit-config.yaml` and the hook ids in it, which git hook
  types are currently installed, and whether a commitizen config exists.
- **FR-7** Language detection uses `git ls-files`, so gitignored content (a vendored
  `node_modules`) cannot decide the project's language. A directory that is not yet a repository
  falls back to a filtered walk.
- **FR-8** A language counts only at **two or more** files, so one stray `.sh` in a Python project
  does not pull in two Docker-backed hooks.
- **FR-9** The `detected` preset is essentials plus the hooks for the languages actually found,
  plus the commitizen hook **only** when a commitizen config exists — the hook runs `cz check`
  and fails every commit without one.
- **FR-10** A suggested `exclude` regex is built from the generated files actually present
  (lockfiles), never from a fixed list, and is offered as a default rather than applied silently.
- **FR-11** A `.pre-commit-config.yml` (the spelling pre-commit does **not** read) is reported as
  a warning: a repository carrying one believes it is configured and is not.
- **FR-12** A config that exists with no installed git hooks is reported as a warning, since that
  is the state in which pre-commit silently does nothing.

### Generated config

- **FR-13** `default_install_hook_types` is **derived** from the stages of the selected hooks,
  never asked about, and omitted when it would equal the default. This is the fix for the single
  most common quiet breakage (see FR-12 and SD-2).
- **FR-14** A hook entry writes only what differs from what the upstream repository already
  declares. In particular `stages` is not restated, so a hook moving stage upstream does not
  require the user's config to be re-edited.
- **FR-15** Scalars are written bare only when doing so provably round-trips through
  `yaml.safe_load`; everything else is single-quoted (YAML's literal form). This keeps shfmt's
  indent width `'2'` a string and an `exclude` regex byte-identical.
- **FR-16** With no hooks selected the file still emits `repos: []` — `repos:` alone parses as
  null, which pre-commit rejects.
- **FR-17** A `ci:` block for pre-commit.ci is optional. When present, its `skip` list is derived:
  exactly the Docker-backed hooks, which pre-commit.ci cannot run.
- **FR-18** The emitted YAML must parse back to the same structure `render.data()` describes.
  Checked by `render.matches()`, asserted for every preset, and re-checked at save time.

### Wizard flow

- **FR-19** The flow is: report the project → choose a preset → adjust the hook checkbox (grouped
  by category, pre-ticked from the preset) → behaviour and excludes → a review loop.
- **FR-20** From the review loop every earlier answer can be revisited, and the config can be
  previewed as a table, as YAML, validated, or resolved against the network.
- **FR-21** The review screen shows: a summary, every hook with its source repository, stage and
  whether it rewrites files; the prerequisites any selected hook needs (Docker, Node, a commitizen
  config); and a warning for every conflicting pair.
- **FR-22** "Refresh every repository to its latest tag" runs the real `pre-commit autoupdate`
  against a sandbox and shows what changed. It is confirmed, never applied silently — autoupdate
  takes the newest tag, which is occasionally a prerelease.
- **FR-23** Nothing in the user's project is written or modified until the user selects "Save".

### Verification

- **FR-24** `validate.verify()` runs `pre-commit validate-config` in a throwaway git repository.
  It is offline and fast, so it runs on demand *and* automatically before saving.
- **FR-25** `verify()` additionally checks that every stage in use appears in
  `default_install_hook_types` — a hook that would never fire is a broken config that nothing
  else reports.
- **FR-26** `validate.resolve()` runs `pre-commit install-hooks`, cloning each repository and
  building its environment. This is the only check that proves hook **ids** exist. It needs the
  network and takes minutes, so it is an explicit menu action only.
- **FR-27** A disagreement at save time is reported and asks for confirmation. It never blocks
  the save — the user's config is the user's to save.
- **FR-28** Without `pre-commit` installed the wizard still works end to end; the checks say so
  rather than failing.

### Saving

- **FR-29** An existing file is copied to `<name>.bak.<timestamp>` before being replaced.
- **FR-30** A config not written by this wizard asks before being replaced, and reports the hook
  ids it currently runs.
- **FR-31** After saving, `pre-commit install` is run with the derived `--hook-type` flags (opt-out
  via the behaviour step). When it is skipped, the exact command to run later is printed.
- **FR-32** `--output <path>` writes elsewhere and never touches the project's own config or its
  git hooks.

---

## 3. Non-Functional Requirements

- **NFR-1** Zero new runtime dependencies. PyYAML, Click, Rich and questionary are already
  present.
- **NFR-2** The unit test suite requires neither `pre-commit` nor the network. Tests needing the
  binary are skipped when it is absent.
- **NFR-3** No import of the wizard at CLI start-up — configurator modules are imported on demand
  through `configure.Configurator.load()`.
- **NFR-4** `verify()` completes in well under a second; anything network-bound is behind an
  explicit action with a spinner and a stated cost.
- **NFR-5** Every failure path in `validate.py` returns `None` or a failed `Check`. A verification
  must never be able to end the wizard.

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should the wizard offer `repo: local` hooks? | **Resolved 2026-08-01 — no.** A local hook is an `entry` + `language` + `files` triple describing a script the wizard cannot see. Out of scope; hand-written. |
| 2 | Should Go and Rust presets ship? | **Resolved 2026-08-01 — not in v1.** The community mirrors for both are unmaintained or archived, and shipping a pin that cannot be verified is worse than omitting the preset. Revisit if a maintained mirror appears. |
| 3 | Should the shipped revs be `--freeze`d to commit SHAs? | **Resolved 2026-08-01 — no.** Tags are what a human can read and what `autoupdate` writes back. The wizard offers to refresh them instead. |
| 4 | Should conflicting hooks be refused rather than warned about? | **Resolved 2026-08-01 — warned.** A user may genuinely want black for formatting and ruff for linting only; the pair is a smell, not an error. Presets are held to the stricter rule (FR-4). |
| 5 | Should an existing config be parsed back into wizard state? | **Open.** Same answer as commitizen for now — the timestamped backup is the safety net. See SD-5. |

---

## 5. Findings That Changed the Design

- **`validate-config` does not check hook ids.** Discovered while building the verification
  layer: a config naming a nonexistent id validates cleanly. This is why `resolve()` exists as a
  separate, slower level rather than everything living in one `verify()`.
- **`pre-commit autoupdate` can move a pin to a prerelease.** Observed live: isort resolved from
  `8.0.1` to `9.0.0b1`. This is why FR-22 shows the diff and asks, rather than applying it.
- **The GitHub tags API is not ordered by version.** It returned an older tag than `autoupdate`
  chose for one repository and a newer one for another. Every rev in `REPOS` is therefore what
  `pre-commit autoupdate` itself resolved, not what the API listed first.
- **`repos:` with an empty list must be spelled `repos: []`.** The bare key parses as null and
  pre-commit rejects it — found by the round-trip assertion (FR-18) on the `empty` preset, not by
  reasoning.
