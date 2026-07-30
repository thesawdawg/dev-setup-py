# Stack Decisions: `devstuff configure starship`

**Date:** 2026-07-30
**Context:** New subsystem inside the existing `devstuff` CLI (Click + Rich + questionary,
YAML-catalog-driven, no per-tool Python classes for *installation*).

---

## SD-1 — Tool-specific Python, not a config DSL in YAML

**Decision: configurators are Python modules registered by tool key.**

The repo's strongest invariant is that adding a *tool* is a YAML edit, because installation
reduces to a handful of mechanisms (`npm`, `apt`, `bash`, …) that generalise cleanly.
Configuration does not reduce that way. Starship's config is a TOML file whose semantics are
format strings, palette tables and powerline transition glyphs; nvm's is a shell export; git's is
`git config` invocations. They share nothing but the word "configure".

- **Rejected — a `config_wizard:` schema in `tools.yaml`.** To express this wizard you would need
  conditional questions, an ordered section model with per-item colour roles, a preset system that
  changes how every other answer renders, and a template language for the output file. That is a
  programming language spelled in YAML, and every future tool would want a different one.
- **Rejected — one giant `configure_cmd.py` with an `if key == "starship"` branch.** Works for
  one tool, becomes the file nobody wants to touch at three.

**Consequence:** `configure/__init__.py` holds a small `CONFIGURATORS` registry (key → label +
callable). Adding a configurator is a new module plus one dict entry — the same
strategy-dispatch shape as `_INSTALLERS` in `generic.py` and `_PRIMITIVES` in the agent. The
generic parts (picker, install-state check, `--print`/`--path`) live in the command and are
shared; only the wizard body is tool-specific.

## SD-2 — The registry is keyed in Python, not declared in the catalog

**Decision: no new `tools.yaml` field.**

A `configure: starship` field was considered so `devstuff list` could flag configurable tools.

- **Rejected** because user catalogs are validated at load time and `SUPPORTED_FIELDS` is
  strict: a user (or an exported/re-imported catalog) could name a configurator that does not
  exist, and the only options would be a new load-time failure mode or silent ignoring. The
  registry already knows the answer, `configure --list` exposes it, and the catalog stays a pure
  description of *installation*.

## SD-3 — Live preview via the real `starship prompt`

**Decision: render the candidate config with the installed starship binary.**

`starship prompt` is a supported, non-interactive entry point (it is what the shell hook calls).
Writing the candidate TOML to a temp file, pointing `STARSHIP_CONFIG` at it and running it inside
a throwaway sample project yields the exact bytes the user's prompt will produce, ANSI and all —
printed into Rich with `Text.from_ansi`.

- **Rejected — approximate the prompt in Rich only.** Cheaper, but it would be a second
  implementation of starship's rendering that drifts the moment a module's default format changes,
  and it would quietly lie about the thing the user is choosing.
- **Rejected — write the real config and tell the user to open a new shell.** That is the loop
  this feature exists to remove, and it mutates state before consent.

**Consequence:** the offline renderer still exists (FR-8) but is explicitly the *fallback*, is
labelled as such in the UI, and is driven by the same `SECTIONS`/`PALETTES` data as the TOML
emitter — so it can be wrong about spacing, never about which sections you picked. It is also
what unit tests exercise, since CI has no starship.

## SD-4 — Emit TOML as text; parse it only in tests

**Decision: hand-write the TOML with a tiny emitter, validate with stdlib `tomllib` in tests.**

- **Rejected — `tomli-w` / `tomlkit`.** A new runtime dependency for every devstuff user, and
  `tomli-w` cannot emit comments at all. The generated file is meant to be read and hand-edited;
  the header comment explaining what produced it and the docs link are part of the deliverable.
- Every value that contains starship grammar (`$module`, `[text]($style)`, `\[`) is emitted as a
  TOML **literal** string (`'…'`), which processes no escapes — so `\[` in `git_status` needs no
  doubling and a basic-string escape bug is structurally impossible. The multi-line top-level
  `format` uses a basic string with `\` line continuations (starship's own preset idiom); it
  contains no backslashes of its own, so that stays safe too.

## SD-5 — One palette schema with semantic roles

**Decision: every palette defines the same nine roles; sections reference roles, not colours.**

Starship palettes map names to colours and substitute anywhere a colour is expected. Naming them
after *meaning* (`dir`, `git`, `lang`, `infra`, `shell`, `ok`, `err`, `muted`, `bar_text`) rather
than appearance (`blue`, `mauve`) means the emitter has exactly one code path for all five
palettes, a new palette is nine hex values, and the saved file is self-documenting.

The `terminal` palette uses ANSI colour names (`blue`, `bright-black`) rather than hex, so it
inherits whatever theme the user's terminal already has — the right default for someone who has
already themed their terminal and does not want a prompt fighting it.

## SD-6 — Wizard state is write-only; no round-trip parse

**Decision: re-running the wizard starts from defaults, not from the existing file.**

The agent wizard seeds its prompts from the existing config, which is easy for five scalar
fields. Reconstructing `StarshipConfig` from an arbitrary `starship.toml` is a different problem:
the file may be hand-edited, may come from an official preset, may use modules the wizard does
not model. A parser that silently discards what it does not understand, then offers to overwrite,
is worse than one that does not exist.

**Consequence:** FR-10's timestamped backup is the safety mechanism, not a merge. The wizard
warns when it is about to replace a file it did not write.

## SD-7 — Sample project fixture over a synthetic prompt string

**Decision: create a real throwaway git repo with language marker files.**

`starship prompt` reads the filesystem to decide what renders. A temp dir named `api`, `git
init -b main`, one staged and one untracked file, plus `package.json` / `pyproject.toml` /
`Cargo.toml` / `go.mod` / `composer.json` / `Gemfile` / `pom.xml`, makes git and package sections
render truthfully. `truncate_to_repo` keeps the preview showing `api` rather than a temp path.

- **Known limit** (recorded in specifications.md §7): a language module also needs its toolchain
  binary, so a selected language shows in the live preview only where that toolchain exists.
  Shimming fake `node`/`python` executables onto `PATH` to fix a *preview* was rejected as
  disproportionate.
