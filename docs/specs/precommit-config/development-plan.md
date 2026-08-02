# Development Plan: `devstuff configure pre-commit`

**Date:** 2026-08-01
**Status:** Milestones 1–5 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Catalog entry | `pre-commit` in `src/dev_setup/tools.yaml` (`type: uvx`) — already present | `devstuff install pre-commit` puts `pre-commit` on the PATH |
| 2 | Verified hook catalog | `configure/precommit/model.py` — `REPOS`, `HOOKS`, `GROUPS`, `PRESETS`, `PreCommitConfig` | Every rev is what `pre-commit autoupdate` resolves to and every id was read from that repo's `.pre-commit-hooks.yaml` at that rev |
| 3 | Emitter | `configure/precommit/render.py` — `to_yaml()`, `data()`, `matches()`, the offline preview | Every preset round-trips (emitted YAML parses to `data()`) and is accepted by `pre-commit validate-config` |
| 4 | Live check | `configure/precommit/validate.py` — sandbox repo, `validate-config`, `install-hooks`, `autoupdate`, `install` | Three verification levels, each degrading to a failed `Check` rather than an exception |
| 5 | Wizard + wiring | `configure/precommit/{detect,wizard}.py`, registry entry, README, CLAUDE.md, this spec | `devstuff configure pre-commit` walks the steps, previews, checks, saves with a backup, and installs the git hooks |

## Testing Strategy

**`tests/test_configure_precommit.py` — unit by default, with the binary-dependent tests skipped
when `pre-commit` is absent (NFR-2).** 70 tests:

- **Model invariants:** every hook points at a real repo, group and stage; keys unique; every
  value emitted into `args` is a `str` (an int would only fail at `validate-config` time); every
  preset and every `LANGUAGE_HOOKS` entry names hooks that exist.
- **Preset safety:** no shipped preset contains a conflicting pair (FR-4), while a user-built
  conflict is still detected — the asymmetry the wizard depends on.
- **`install_hook_types` derivation:** the default case emits nothing; a `commit-msg` hook adds
  its own type and reaches the file; a `pre-push` hook likewise; `manual` never becomes an install
  type. This is SD-2, and it is the one derivation that is invisible when it is wrong.
- **Rendering:** every preset round-trips through `yaml.safe_load` (the SD-3 guarantee);
  shfmt's `'2'` stays a string; a rev of `1.0` stays a string; an `exclude` regex comes back byte
  identical, including one containing a quote; an empty hook list still produces `repos: []`;
  hooks are grouped one entry per repo, in the model table's order.
- **`hook_entry` minimalism:** a hook whose upstream declares its own stages emits `{id}` alone
  (FR-14).
- **pre-commit.ci:** the block is absent unless asked for; `skip` lists exactly the Docker-backed
  hooks; no `skip` key when nothing needs skipping.
- **Detection, against real temporary git repositories:** the two-file language threshold; a
  Dockerfile matched by name rather than suffix; `suggested_exclude` naming only files that exist
  and compiling to a regex that matches them; gitignored `node_modules` not deciding the language
  (FR-7); the `.yml` spelling reported; a hand-written config's ids read; a broken config
  surviving; `hooks_installed` distinguishing a real pre-commit hook from a `.sample` and from
  somebody else's `commit-msg`.
- **The commitizen coupling:** the commit-msg hook is suggested only when a commitizen config
  exists (FR-9) — without one it fails every commit.
- **Saving:** the file is written; an existing file is backed up with its content intact; the
  generated header is what the overwrite guard keys on.
- **Against the real binary (skipped without it):** every preset passes `pre-commit
  validate-config` with a `ci:` block and an `exclude` set, and the stage check reports a
  commit-msg hook as reachable.

**Verified by hand, end to end** (the interactive flow cannot be driven from a piped shell — a
`pty.fork()` was used, as for the starship wizard):

1. In a throwaway repo containing Python files, a `uv.lock` and a `.cz.toml`, the wizard detected
   `python: 2`, suggested the `detected` preset with ruff plus the commitizen hook, and proposed
   an `exclude` naming `uv.lock` only.
2. Saving wrote the config, ran `pre-commit install`, and installed **both** `pre-commit` and
   `commit-msg` git hooks.
3. A commit with the message `oops no convention` was rejected by the commit-msg hook; the same
   commit with `feat: add new module` passed. This is SD-2 demonstrated end to end — with the
   derivation absent, that first commit would have succeeded.
4. `ruff-format` rewrote a file and stopped the commit, which is the fixer behaviour the review
   screen describes.

## Risks

| Risk | Mitigation |
|------|------------|
| **Shipped revs go stale.** A pin correct at release is old a month later. | "Refresh every repository to its latest tag" runs the real `autoupdate` (SD-8). The pins are a starting point, not a claim about today. |
| **A hook id changes or a repository is archived upstream.** `validate-config` would not notice. | `validate.resolve()` is the check that would (FR-26). The `prettier` mirror was already switched once for this reason — upstream `pre-commit/mirrors-prettier` is archived. |
| **A user's hand-edited config is replaced.** | Timestamped backup, an overwrite prompt listing the ids currently in force, and the generated-header check so the wizard's own output is replaced without ceremony (FR-29/30). |
| **Docker-backed hooks fail on a machine without Docker.** | `Hook.needs` surfaces on the review screen, and drives the pre-commit.ci `skip` list (SD-7). |
| **A first run rewrites hundreds of files.** | Presets are deliberately small, and the review screen names every hook that rewrites files and explains that pre-commit stops the commit when one does. |
| **The catalog and the real repositories drift.** | Every id and rev was read from the repositories at authoring time; `resolve()` re-checks on demand. A future canary job could run `resolve()` over every preset on a schedule — not built. |

## Not built

- An `add`-style wizard for hooks outside the catalog, and `repo: local` hook authoring
  (Open Question 1).
- Go and Rust presets (Open Question 2 — no maintained mirror to pin).
- Reading an existing config back into wizard state (Open Question 5, SD-6).
- A scheduled canary running `validate.resolve()` over every preset to catch upstream drift.
