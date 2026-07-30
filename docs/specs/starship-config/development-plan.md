# Development Plan: `devstuff configure starship`

**Date:** 2026-07-30
**Status:** Milestones 1–5 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Data model | `configure/starship/model.py` — `Preset`, `Palette`, `Section`, `StarshipConfig`, the ordered `SECTIONS` tuple | Defaults produce a coherent config with no I/O |
| 2 | TOML emitter | `configure/starship/render.py` `to_toml()` | Output parses under `tomllib` for every preset × palette × layout combination, and `starship print-config` accepts it |
| 3 | Preview | `configure/starship/preview.py` (live, via `starship prompt` in a sample project) + `render.sample_markup()` (offline fallback) | Live path prints ANSI for a real config; offline path renders every selected section with starship absent |
| 4 | Wizard + command | `configure/starship/wizard.py`, `commands/configure_cmd.py`, registry in `configure/__init__.py`, CLI wiring | `devstuff configure starship` walks the steps, previews after each, saves with a backup |
| 5 | Integration + docs | post-install offer in `install_cmd.py`, help text, README, CLAUDE.md, this spec | `devstuff install starship` offers the wizard; docs describe how to add a configurator |

## Testing Strategy

`tests/test_configure_starship.py` — unit only, no starship required (NFR-5):

- **Emitter validity:** every `preset × palette × layout` combination parses under `tomllib`;
  spot-check that `format`, `palette`, `add_newline` and the expected module tables exist.
- **Section selection:** unselected modules get no table and never appear in `format`; selected
  ones appear in canonical order regardless of the order the checkbox returned them in.
- **Symbol handling:** `plain` preset emits `symbol = ''` for icon-bearing sections (regression
  guard for starship's default glyph leaking through); `icons`/`powerline` emit the Nerd Font one.
- **Powerline runs:** consecutive same-role sections produce one bar run — the transition count
  equals the number of role changes, not the number of sections.
- **Defaults on by default:** `disabled = false` is emitted for `kubernetes`/`time` when selected
  (FR-16) and never for modules starship already enables.
- **Layout:** `two_line`/`two_line_right` place `$line_break` before `$character`; `single` does
  not; `two_line_right` moves Shell-group sections into `right_format` and out of `format`.
- **Offline renderer:** `sample_markup()` mentions every selected section's sample value and is
  valid Rich markup (rendered through a `Console` without raising).
- **Save path:** `save()` honours `STARSHIP_CONFIG`, backs an existing file up to a timestamped
  name, and leaves the backup byte-identical to the original.
- **Wizard flow:** scripted `ui.*` prompts (the `FakePrompts` pattern from `test_agent_wizard.py`)
  drive a full run and assert the written file reflects every choice; a cancel writes nothing.
- **Registry:** `configure.get()` / `keys()` resolve starship and reject unknown keys.

Manual verification (documented, not automated — CI has no starship):
`starship print-config` against a generated file for each preset, and a visual check of the live
preview for `plain` / `icons` / `powerline`.

## Risks

| Risk | Mitigation |
|------|------------|
| `starship prompt` flags differ across versions (`--terminal-width`, `--jobs`) | Try the full flag set, retry with a minimal one, then give up to the offline renderer. Never fatal (NFR-4). |
| Nerd Font glyphs wrong or absent in the user's terminal | Glyphs are defined as explicit `\uXXXX` escapes with the Nerd Font name in a comment, so they are reviewable; the `plain` preset is the escape hatch and the two Nerd Font presets say so at the point of choosing. |
| Preview subprocess hangs | 5 s timeout; timeout is treated as a failed render. |
| Overwriting a hand-tuned `starship.toml` | Timestamped backup before write, path reported; the wizard warns when replacing a file it did not generate (no devstuff header). |
| Sample project creation fails (no `git`) | Wrapped in try/except; git sections then simply do not render in the live preview, which is the same degradation as running outside a repo. |
| Emitter and offline renderer drifting apart | Both consume the same `SECTIONS`/`PALETTES` tables; neither hard-codes a module list. |
