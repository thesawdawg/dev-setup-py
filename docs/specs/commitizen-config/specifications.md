# Specification: `devstuff configure commitizen`

**Date:** 2026-07-31
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

`devstuff install commitizen` puts `cz` on the PATH. That is where it stops. Commitizen does
nothing at all until a config file exists, and its `cz init` writes a five-key stub for
`cz_conventional_commits` — which is fine right up to the moment somebody wants a type
commitizen does not ship, or wants `docs:` to stop triggering releases, or wants their
changelog headings to say something other than "Feat".

Everything past that stub means `cz_customize`, and `cz_customize` is not a setting — it is
nine coupled regexes and an ordered map. The user has to write a `bump_pattern` whose **group
1** is what `bump_map`'s keys get `re.match`ed against, keep a `schema_pattern` in step with a
Jinja2 `message_template` they also wrote, and put the two breaking-change rules at the *top*
of `bump_map`, because commitizen stops at the first key that matches. Get the order wrong and
`feat(api)!:` quietly ships as a minor release. Nothing tells you; the version is just wrong.

`devstuff configure commitizen` replaces that with a wizard: choose the commit types, say what
each one does to the version and where it lands in the changelog, and see the resulting bump
table and changelog sections before anything is written — with the rules replayed through the
real `cz` to confirm commitizen agrees.

**Success criteria**
- A user who has never read commitizen's config reference gets a working `cz_customize` setup,
  with their own types and bump rules, in under two minutes.
- The bump table the wizard shows is the bump commitizen actually performs — checked by
  replaying commits through `cz bump --dry-run`, not asserted from intent.
- Nothing is written until the user confirms; an existing config is never lost, and a
  `pyproject.toml` is edited in place rather than rewritten.
- The generated file is one a human can then hand-edit: commented, ordered, and using
  commitizen's own key names rather than a devstuff-specific encoding.
- Zero new runtime dependencies.

**Non-goals**
- A general-purpose config-wizard DSL declared in `tools.yaml` (SD-1, inherited from starship).
- Round-tripping a hand-written config back into wizard state (SD-6).
- `cz_jira` and third-party commitizen plugins (SD-8).
- Writing JSON or YAML configs. commitizen reads `.cz.json`/`.cz.yaml`, but TOML is the form
  every commitizen example is written in and the only one `pyproject.toml` can hold (SD-7).
- Changelog *templates* (commitizen's `template`/`extras` Jinja2 hooks). Section names and
  ordering are configurable here; the markdown around them is not.
- Running `cz bump` for the user. The wizard configures releases; it does not cut one.

## 2. Users & Personas

| Persona | Description | Primary needs |
|---------|-------------|---------------|
| New commitizen user | Just ran `devstuff install commitizen` | A conventional-commits setup that works, without reading the manual |
| Team lead | Wants house rules — a `deps:` type, `docs:` never releasing | Custom types and per-type bump levels, enforced on commit |
| Polyglot maintainer | Rust/Node/PHP repo, not Python | A standalone config, the right version provider, semver not pep440 |
| Existing user | Has a hand-written `[tool.commitizen]` | To not lose the rest of `pyproject.toml`, and to be told what is being replaced |

## 3. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-0 | Every generated config is loaded by the real `cz` without error, and produces exactly the increments the wizard's bump table claims, for every convention × provider × scheme × tag-format combination under test. | Must |
| FR-1 | `commitizen` is a built-in catalog tool (`type: uvx`, `pip_name: commitizen`, `check_cmd: cz`, requires `uv`), so `devstuff install commitizen` works and `devstuff configure` offers the wizard afterwards. | Must |
| FR-2 | The wizard covers: commit convention, which commit types exist, what each bumps, changelog membership and heading per type, version provider/scheme/tag format/version files, release toggles, the questions `cz commit` asks, and the destination file. | Must |
| FR-3 | Two conventions — `cz_conventional_commits` (commitizen's own fixed rules, no `customize` block emitted) and `cz_customize` (everything configurable). | Must |
| FR-4 | The built-in type table reproduces `commitizen.defaults.BUMP_MAP` and `ConventionalCommitsCz`'s picker exactly: the nine conventional types selected, `feat`→MINOR, `fix`/`refactor`/`perf`→PATCH, everything else no-release, and `feat`/`fix`/`refactor`/`perf` in the changelog. | Must |
| FR-5 | Additional types (`chore`, `revert`, `deps`, `security`) are offered unselected; the user can also add their own with a key, description, bump level, changelog heading and hotkey. | Must |
| FR-6 | The generated `bump_map` lists the two breaking-change rules (`^.+!$`, `^BREAKING[\-\ ]CHANGE`) first, since commitizen stops at the first matching key. | Must |
| FR-7 | `bump_map_major_version_zero` is always emitted, derived from `bump_map` by demoting MAJOR to MINOR. | Must |
| FR-8 | `schema_pattern` accepts every message `message_template` can produce, including the breaking form, and always accepts the `bump:` prefix commitizen's own release commit uses. | Must |
| FR-9 | The review screen shows: a summary, a sample `cz commit` message, a bump table (`type → increment → resulting version` against 1.4.2), and the changelog sections with the types filed under each. | Must |
| FR-10 | "Check these rules against the real cz" builds a throwaway git repo tagged at 1.4.2, replays one commit per distinct bump level plus both breaking forms through `cz bump --dry-run`, and reports agreement or disagreement per rule. | Must |
| FR-11 | The same check runs automatically before saving. A disagreement is reported and requires confirmation, but never blocks the save — the user's config is the user's. | Must |
| FR-12 | "Preview a generated changelog" runs the real `cz changelog --dry-run` over one sample commit per selected type. | Should |
| FR-13 | The wizard detects, and defaults from: git root, existing commitizen configs (in commitizen's own search order, skipping files without a commitizen section), version provider and current version from project files, latest git tag, and an existing changelog file. | Must |
| FR-14 | Writing to `pyproject.toml` replaces every `[tool.commitizen…]` table and leaves the rest of the file byte for byte. The result is verified by parsing it back; if it does not match, the write falls back to a standalone `.cz.toml` and the original file is untouched. | Must |
| FR-15 | Any file about to be overwritten is copied to `<name>.bak.<timestamp>` first. A config not written by this wizard prompts before being replaced. | Must |
| FR-16 | Nothing is written until the user picks "Save"; "Cancel" writes nothing. | Must |
| FR-17 | After saving, if more than one commitizen config now exists in the project, the wizard names them and says which one commitizen will actually read. | Must |
| FR-18 | After saving into a git repo with no `commit-msg` hook, the wizard offers to install one running `cz check --allow-abort --commit-msg-file "$1"`. An existing hook is never replaced. | Should |
| FR-19 | `devstuff configure commitizen --path` prints the config file commitizen would read here, or where the wizard would create one. | Must |
| FR-20 | `devstuff configure commitizen --output PATH` writes a standalone config to `PATH` and touches nothing in the project — no destination question, no shadowing warning, no git hook. | Must |
| FR-21 | Without `cz` installed, the wizard still runs end to end; the live checks say so rather than failing. | Must |
| FR-22 | Two selected types sharing a hotkey is reported — commitizen does not validate it, and the second type simply becomes unreachable. | Should |
| FR-23 | Deselecting every type keeps the previous selection: an empty alternation would match everything. | Must |

## 4. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | No new runtime dependencies. TOML is emitted as text and read back with stdlib `tomllib`. |
| NFR-2 | A live check completes in a few seconds (~8 `cz` invocations); it is an explicit menu action plus one automatic run at save time, never on every redraw. |
| NFR-3 | Every live-check failure path degrades to the offline table. A check must never be able to end the wizard. |
| NFR-4 | The generated file is stable: the same answers produce the same bytes, and re-splicing an already-spliced `pyproject.toml` is a no-op. |
| NFR-5 | Unit tests run without `cz` installed. The commitizen-backed tests are marked `integration`. |

## 5. Verification

| Requirement | How it is verified |
|-------------|--------------------|
| FR-0, FR-10 | `tests/integration/test_commitizen_config.py` — seven configs replayed through the real `cz` |
| FR-4 | `test_defaults_reproduce_commitizens_own_rules`, against constants read out of the installed package |
| FR-6, FR-7 | `test_bump_map_leads_with_the_breaking_rules`, `test_major_version_zero_demotes_only_the_breaking_rules` |
| FR-8 | `test_schema_pattern_accepts_the_wizards_own_messages`, `test_schema_pattern_always_accepts_commitizens_own_bump_commit` |
| FR-13 | `test_the_version_provider_is_read_off_the_project`, `test_existing_configs_follow_commitizens_search_order` |
| FR-14 | `test_splice_*` — including the fail-closed case and idempotency |
| FR-15, FR-18 | `test_save_backs_up_what_was_there`, `test_an_existing_commit_hook_is_never_touched` |
| FR-17 | `test_shadowing_another_config_is_called_out` |
| FR-22, FR-23 | `test_duplicate_shortcuts_are_reported`, `test_deselecting_everything_keeps_the_previous_types` |

## 6. Open Questions

| Question | Status |
|----------|--------|
| Should the wizard read an existing config back into its state? | **Resolved 2026-07-31 — no.** See SD-6: partial reconstruction is worse than none, and the timestamped backup covers the real need. |
| Should a disagreement from the live check block saving? | **Resolved 2026-07-31 — no.** FR-11: it warns and asks. A check that can veto a save turns a helpful cross-check into an obstacle the moment commitizen changes. |
| Should `pre_bump_hooks`/`post_bump_hooks` be configurable? | **Deferred.** They are shell commands with eight environment variables; a text prompt for them would be a worse editor than `$EDITOR`. Hand-editable in the generated file. |
| Should the wizard offer a `.pre-commit-config.yaml` entry as well as a git hook? | **Deferred.** It means owning a second file's merge semantics; the git hook covers the same need with no new file format. |
