# Stack Decisions: `devstuff configure pre-commit`

**Date:** 2026-08-01
**Context:** The third configurator. SD-1 and SD-2 in `docs/specs/starship-config/` (configurators
are Python modules in a registry; the registry is keyed in Python rather than declared in
`tools.yaml`) apply here unchanged and are not restated. Everything below is specific to
pre-commit.

---

## SD-1 — The wizard manipulates a curated hook catalog, not arbitrary YAML

**Decision: the primary object is a list of hooks drawn from a fixed, verified catalog. The user
picks from it; they do not describe repositories.**

A `.pre-commit-config.yaml` is a list of `(repository, rev, [hook ids])`. Every part of that is a
fact about the outside world — which repository publishes a hook, what its id is, what tag is
current — and none of it is derivable from the user's project. A wizard that asked for them would
be a YAML form with extra steps.

- **Rejected — prompt for repo URL, rev and ids.** This is the config file spelled as questions.
  The user still has to know everything, and now they type it into questionary instead of an
  editor with syntax highlighting.
- **Rejected — query the pre-commit hook index at run time.** There is no such registry with
  stable semantics; the closest thing is a page of links. It would also make the wizard
  network-dependent for its core function, which NFR-4 rules out.
- **Rejected — vendor the full hook list from every popular repo.** `pre-commit-hooks` alone ships
  34 hooks, most of which nobody wants. A curated subset with a description of what each one
  actually does is the value being added.

**Consequence:** `model.py` holds `REPOS`, `HOOKS` and `PRESETS`, and everything else reads them.
Adding a hook is one record. The cost is that a hook outside the catalog means hand-editing the
generated file afterwards — which is why the output is written to be hand-editable (SD-3).

## SD-2 — `default_install_hook_types` is derived, never asked

**Decision: the git hook types to install are computed from the stages of the selected hooks.**

This is the wizard's single highest-value derivation, and it exists because of a specific trap.
`pre-commit install` installs only the `pre-commit` git hook unless given `--install-hook-types`.
So a user who adds commitizen's `commit-msg` hook gets a config that validates, an install that
reports success, and a hook that never runs. Nothing in pre-commit's output mentions it.

- **Rejected — ask the user which hook types to install.** It is a question about the mechanism
  rather than the intent, and the correct answer is fully determined by the hooks already chosen.
  Asking it invites a wrong answer that produces a silently inert config.
- **Rejected — always write every hook type.** Installing a `pre-push` hook for a config with no
  pre-push hooks means git invoking pre-commit on every push to do nothing.

**Consequence:** `PreCommitConfig.install_hook_types()` is the one source, and it feeds three
places: the emitted `default_install_hook_types`, the `pre-commit install --hook-type` flags, and
a check in `verify()` asserting no selected stage is missing from it. The key is omitted from the
file entirely when it equals the default, so a simple config stays simple.

## SD-3 — YAML is emitted as text, and verified by parsing it back

**Decision: `render.to_yaml()` writes the file by hand; `render.data()` describes the same content
as a structure; `render.matches()` asserts the first parses to the second.**

The comments are part of the deliverable. A generated config that people are expected to go on
hand-editing has to say what each block is for and why `default_install_hook_types` is there at
all — and no YAML dumper can emit interleaved comments. But a hand-written emitter is exactly
where a quoting bug produces a file that is valid YAML and says the wrong thing.

- **Rejected — `yaml.safe_dump`.** Loses every comment, reorders nothing usefully, and quotes to
  its own taste. The output is correct and unreadable.
- **Rejected — ruamel.yaml round-trip mode.** It genuinely preserves comments, and it is a new
  runtime dependency on a globally-installed CLI for one wizard (NFR-1). Rejected on the same
  grounds as the Ollama package in the agent spec.
- **Rejected — a Jinja template.** Moves the quoting bugs into a file with no tests and no types.

**Consequence:** the two representations are checked against each other for every preset, and the
check runs again at save time as the first entry in `verify()`. The `empty`-preset `repos: []` bug
was found by this and nothing else.

## SD-4 — Quoting is decided by measurement, not by a table

**Decision: a scalar is written bare only if a character-class check passes *and*
`yaml.safe_load(value) == value`.**

YAML's implicit typing is the classic footgun: `2` becomes an int, `1.0` becomes a float, `no`
becomes `False`, `1:30` becomes a sexagesimal integer in YAML 1.1. pre-commit's schema requires
strings in `args`, so shfmt's indent width `'2'` must survive as a string. Enumerating the rules
by hand means maintaining a list of coercions that PyYAML already implements.

- **Rejected — a hand-maintained reserved-word and pattern list.** It is the same list PyYAML
  already has, kept in a second place, guaranteed to drift.
- **Rejected — quote everything.** Safe and correct, but `rev: 'v6.0.0'` differs from what
  `pre-commit autoupdate` writes back, so the file churns the first time anyone runs it.

**Consequence:** `_str()` asks the parser. This is the same instinct as SD-5 in the commitizen
spec — measure against the real implementation rather than reproduce its rules.

## SD-5 — Verification is split into three levels by cost

**Decision: `verify()` (offline, milliseconds, automatic), `resolve()` (network, minutes, explicit
menu action), `autoupdate()` (network, seconds, explicit menu action).**

The commitizen configurator has one live check that runs at save time. That worked because
`cz bump --dry-run` is fast and offline. pre-commit's equivalents are not equal: `validate-config`
is instant, but proving a hook id exists means cloning the repository and building its
environment, which is minutes on a cold cache.

Collapsing these would mean either a save-time check that takes five minutes, or never checking
hook ids at all. Both are worse than making the cost visible.

- **Rejected — run `pre-commit run --all-files` in the sandbox.** It proves the most, and it runs
  the user's linters against a fake repository, so its output is noise about a project that does
  not exist.
- **Rejected — skip id verification entirely.** `validate-config` never opens a repository, so a
  typo would reach the user's first commit. The catalog is verified at authoring time, but a user
  who edits args or a rev deserves a way to re-check.

**Consequence:** `verify()` is cheap enough to run before every save (FR-24), and the review menu
carries "Clone the repos and prove every hook id exists (slow)" with the cost stated in the
confirmation.

## SD-6 — An existing config is read for reporting, never for reconstruction

**Decision: `detect.existing_hook_ids()` extracts ids to tell the user what is about to be
replaced. Wizard state is never rebuilt from a config on disk.**

A real `.pre-commit-config.yaml` can carry `repo: local` hooks, per-hook `files`/`exclude`
overrides, `additional_dependencies` pinning a plugin tree, and repositories this catalog has
never heard of. Round-tripping that into the model would silently drop whatever did not fit, and
the user would only find out later.

- **Rejected — partial round-trip, keeping what is recognised.** The failure is silent and the
  loss is invisible: the wizard would show a config that looks right and quietly discard the
  local hook the project depends on.

**Consequence:** the same answer as commitizen's SD-6 — a timestamped backup is the safety net,
the overwrite prompt lists the hook ids currently in force, and re-running the wizard on a
hand-tuned config is an explicit replacement rather than an edit. This is Open Question 5.

## SD-7 — Docker-backed hooks ship, with their cost stated

**Decision: `shellcheck`, `shfmt` and `hadolint` are in the catalog, each carrying
`needs="docker"`, which drives both a review-screen warning and the pre-commit.ci `skip` list.**

These are the best available hooks for their languages, and the alternatives are worse: the
system-binary variants require the user to have installed the tool themselves, which a wizard
cannot arrange.

- **Rejected — omit them.** Leaves shell scripts and Dockerfiles unlinted, which is most of what
  the "shell" and "docker" cases are.
- **Rejected — ship them silently.** The first commit on a machine without Docker fails with a
  container error that says nothing about pre-commit.

**Consequence:** `Hook.needs` is a first-class field. It surfaces on the review screen under
"needs to be available", and `render.ci_skip()` derives the pre-commit.ci skip list from it —
pre-commit.ci has no Docker daemon, so those hooks must be skipped there or every CI run fails.

## SD-8 — Pinned revs are shipped and refreshable, not resolved at run time

**Decision: `REPOS` carries a verified tag per repository, and the wizard offers to refresh every
pin through the real `pre-commit autoupdate`.**

A pinned rev is the point of a pre-commit config: hooks that change under you are hooks that break
builds on unrelated commits. But a table of tags inside a released package is stale the week after
it ships.

- **Rejected — resolve every rev at wizard run time.** Makes the wizard's core path network-bound
  and slow, and produces a different config on Tuesday than on Monday.
- **Rejected — omit `rev` and let pre-commit choose.** `rev` is required; there is no such mode.
- **Rejected — reimplement tag resolution against the GitHub API.** Measured and rejected on
  evidence: the tags endpoint is not version-ordered, and returned both an older and a newer tag
  than `autoupdate` picked for different repositories. `autoupdate` is the reference
  implementation of this question.

**Consequence:** the shipped pins are a correct starting point, and "Refresh every repository to
its latest tag" is one menu action away. Because autoupdate can select a prerelease, the result is
shown as a diff and confirmed rather than applied.
