# Development Plan: `devstuff configure commitizen`

**Date:** 2026-07-31
**Status:** Milestones 1–5 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Catalog entry | `commitizen` in `src/dev_setup/tools.yaml` (`type: uvx`), CI matrix row, README table | `devstuff install commitizen` puts `cz` on the PATH; `devstuff list` shows its version |
| 2 | Data model | `configure/commitizen/model.py` — `ChangeType`, the ordered `TYPES` table, providers/schemes/tag formats, `CommitizenConfig` | Defaults reproduce `commitizen.defaults.BUMP_MAP` and the conventional picker, with no I/O |
| 3 | Emitter | `configure/commitizen/render.py` — the nine derived rules, `to_toml()`, `splice_pyproject()` | Output parses under `tomllib` for every convention × provider; splicing this repo's own `pyproject.toml` leaves every other table byte for byte |
| 4 | Live check | `configure/commitizen/validate.py` — sandbox repo, `cz bump --dry-run` replay, `cz check`, `cz changelog --dry-run` | Seven configs replay through the real `cz` with every rule agreeing |
| 5 | Wizard + wiring | `configure/commitizen/{detect,wizard}.py`, registry entry, README, CLAUDE.md, this spec | `devstuff configure commitizen` walks the steps, previews, checks, and saves with a backup |

## Testing Strategy

**`tests/test_configure_commitizen.py` — unit only, no `cz` required (NFR-5).** 114 tests:

- **Model invariants:** every type key is regex-safe (it is spliced raw into four patterns);
  shortcuts unique; bump levels known; `SAMPLE_BUMPS` is a real semver walk.
- **Agreement with commitizen's shipped defaults:** the selected set, bump map and changelog set
  are compared against constants transcribed from the installed package, and `CONFIG_FILES` is
  compared against `commitizen.defaults.CONFIG_FILES` directly — the precedence warning is only
  correct if that list is.
- **TOML validity:** every convention × provider parses; regexes come back out of the parser
  byte-identical (the literal-string guarantee); a quote in a user-supplied section name does not
  produce an unparseable file.
- **The generated rules, executed in Python:** `bump_pattern` group 1 is fed through `bump_map`
  the way commitizen does it, and the result is asserted for `feat`, `feat(api)`, `docs`,
  `feat(api)!`, `docs!`, and both spellings of the breaking footer. This is the offline half of
  the FR-0 guarantee; the sandbox is the other half.
- **Cross-consistency:** the bump table shown on screen is re-derived from the emitted map and
  compared; `schema_pattern` is matched against messages produced by `message_template`; turning
  off a question removes both the prompt and its template slot.
- **Splicing:** append, replace, idempotency, other tables preserved, a similarly named table
  (`[tool.commitizen_helper]`) not mistaken for ours, and the fail-closed case — a table header
  inside a multi-line string returns `None` and the original file is untouched.
- **Detection:** provider and version read off six project shapes; a `pyproject.toml` with no
  commitizen section is not a config; a malformed file is not a config; JSON and YAML configs
  recognised.
- **Interactive steps:** the prompt helpers are driven through a scripted `ui` double, covering
  the add-a-type validation paths, bump-level overrides, the empty-selection guard, the release
  toggles, the commit hook (written executable; an existing one never touched) and the shadowing
  warning.

**`tests/integration/test_commitizen_config.py` — marked `integration`, needs `cz` and `git` but
*not* sudo or the network** (unlike the rest of that directory — noted in its docstring). 12
tests, ~24s. Seven configs (conventional, custom defaults, `major_version_zero`, pep440 with bare
tags, no scope/body/footer, a two-type minimum, renamed sections with `docs` promoted to PATCH)
are each replayed through `cz bump --dry-run`; plus a user-added type, the changelog headings,
the exclusion of non-changelog types, a negative `cz check`, and `cz info` round-tripping the
file through commitizen's own parser.

**Manual verification.** The full wizard was driven end to end through a forked pty (the
technique CLAUDE.md describes for the starship wizard) in a scratch git repo tagged `v0.3.0`:
version and tag format were picked up from the tag, the review screen rendered, the automatic
pre-save check reported "8 rules agree", and the written file was accepted by `cz --config …
example`.

## Risks

| Risk | Mitigation |
|------|-----------|
| commitizen changes `find_increment`'s first-match-wins semantics, or how `bump_map` keys are matched | The integration suite replays real commits; a change shows up as a failed check rather than as wrong version numbers in the field. The wizard's pre-save check gives the same signal to users. |
| commitizen changes its shipped defaults | `test_defaults_reproduce_commitizens_own_rules` and `test_config_file_order_matches_commitizens` compare against the installed package. |
| The pyproject splice damages a user's file | The result is parsed back and compared to the intended settings; a mismatch aborts to `.cz.toml` and leaves the original untouched (FR-14). Every write is backed up first. |
| `cz_customize` is renamed or removed upstream (commitizen's own docs warn of this — issue #1385) | The convention name lives in one place, `CONVENTIONS` in `model.py`. A rename is a one-line change plus a spec note. |
| A user's own `commit-msg` hook is clobbered | The offer is only made when no hook exists (FR-18). |
| The live check makes the wizard feel slow | It is an explicit action plus one automatic run at save (NFR-2), never on redraw. |

## Follow-ups (not built)

- Reading an existing config back into wizard state (SD-6 — deliberately out).
- `pre_bump_hooks` / `post_bump_hooks` prompts (see the open questions in `specifications.md`).
- Changelog template (`template`/`extras`) configuration.
- A `.pre-commit-config.yaml` entry alongside the git hook (SD-9).
- Configurators for anything other than starship and commitizen.
