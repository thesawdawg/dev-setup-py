# Development Plan: `devstuff configure bat`

**Date:** 2026-08-02
**Status:** Milestones 1–5 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Catalog entry | `bat` in `src/dev_setup/tools.yaml` (`type: bash`) — already present | `devstuff install bat` puts `bat` on the PATH |
| 2 | Measured catalog | `configure/bat/model.py` — `THEMES`, `COMPONENTS`, `SETTINGS`, `SHELL_BITS`, `PRESETS`, `BatConfig` | Components come from `bat --help`; theme modes are hand-corrected against a luminance measurement; the conflict table comes from an exhaustive pair sweep |
| 3 | Emitter and parser | `configure/bat/render.py` — `flags()`, `to_text()`, `parse()`, `matches()` | Every preset round-trips, and a theme name with spaces survives quoting |
| 4 | Live preview and checks | `configure/bat/preview.py` — `render_live()`, `check()`, `describe()` | The candidate renders through the real bat with `BAT_CONFIG_PATH`, and a nonexistent theme is caught |
| 5 | Detection, wizard, wiring | `configure/bat/{detect,wizard}.py`, registry entry, README, CLAUDE.md, this spec | `devstuff configure bat` previews, checks, saves with a backup, and patches `~/.bashrc` |

## Testing Strategy

**`tests/test_configure_bat.py` — unit by default, with the bat-dependent tests skipped when
the binary is absent (NFR-2). 72 tests:**

- **Model invariants:** every theme has one of three modes; both light and dark have options
  to offer (the auto-theme step asks for one of each, so an empty side would be a dead
  prompt); the default components are all real; every preset names real fields and real
  components.
- **The two classification findings, asserted rather than commented:** the Solarized pair is
  classified by name because luminance gets it wrong, and the three ANSI themes are neither
  light nor dark.
- **Conflicts:** no shipped preset contains a conflicting pair (FR-3); a user-built one is
  still detected; the conflict table only names real components.
- **Shell integration constraints:** no `SHELL_BITS` line is blank or contains a newline, and
  the rendered block has no blank lines — the `base.remove_bashrc_block` hazard, asserted
  rather than hoped for.
- **Rendering:** every preset round-trips; a theme with spaces is quoted and parses back
  identically; defaults are omitted (including `--theme=auto` alone and the default component
  set); no components emits `plain`; the generated header is present and is a comment;
  unmodelled lines are carried through.
- **Parsing:** comments and blank lines ignored; unknown options kept as extra; the
  space-separated form (`--theme "Nord"`) handled, since bat accepts it; an unbalanced quote
  survives as an extra line rather than raising.
- **Reading an existing config back:** a full round trip; `plain` becomes no components; a
  non-numeric tab width is ignored; a theme pair on disk implies auto mode.
- **Theme validation:** an unknown theme is caught; both halves of an auto pair are checked; a
  user-built theme is accepted; the check is silent when the list could not be read; the
  shipped table is the fallback.
- **Saving:** the file is written and its parent created; an existing file is backed up with
  content intact; the generated header is what the overwrite guard keys on.
- **Against the real binary (skipped without bat):** the live theme list is non-empty; **every
  shipped theme still exists in this bat**, so the fallback table cannot silently rot; every
  preset renders without a complaint; the preview actually contains the sample's text; `plain`
  produces no line numbers and `numbers` does; **real bat warns rather than failing on an
  unknown theme** — the measurement the whole theme check is built on, asserted so that a
  future bat changing it is caught; and **real bat still complains about `grid` + `rule`**, so
  a fixed conflict is caught rather than silently over-warned about.

**Verified by hand, end to end** (a `pty.fork()`, as for the other wizards):

1. The wizard reported bat 0.26.1 with 28 themes available and no existing config.
2. Choosing the "Code review" preset and a light/dark theme pair, the review screen rendered
   the sample file **through the real bat** — line numbers, grid, file header, file size and
   true-colour syntax highlighting, all drawn by bat rather than approximated.
3. Saving wrote a config whose `--style` and `--theme-dark`/`--theme-light` matched the
   choices, with the generated header on top.
4. The saved file was then handed back to bat independently
   (`BAT_CONFIG_PATH=<file> bat --decorations=always`) and produced exactly the decorations
   chosen — grid, filename, file size and numbers — with empty stderr.

## Risks

| Risk | Mitigation |
|------|------------|
| **A theme in the shipped table disappears upstream.** The fallback would then offer a theme that silently does nothing. | `test_every_shipped_theme_still_exists_in_this_bat` fails when the table names something the installed bat lacks. The live list is preferred in any case (SD-1). |
| **A saved theme name is wrong and nothing says so.** | The whole point of FR-8: every name is checked against `bat --list-themes` before saving, because bat itself only warns and exits 0. |
| **`BAT_THEME` in the user's shell makes the config inert.** | Cleared for the preview so it cannot lie, and reported on the review screen so the user knows (SD-4). |
| **Re-running the wizard leaves a stale `~/.bashrc` block.** | The old block is removed before the new one is written — `base.patch_bashrc` is a no-op when its marker is present, so without this the second run would do nothing (FR-24). |
| **A blank line in a shell bit orphans the rest of `~/.bashrc` on removal.** | Asserted by two tests. This is the documented `remove_bashrc_block` hazard, shared with the functions subsystem. |
| **bat changes its default components or theme handling.** | Only non-defaults are written, so a changed default reaches the user rather than being frozen; and `DEFAULT_COMPONENTS` is read from `bat --help` rather than recalled. |

## Not built

- `--map-syntax` authoring (Open Question 1) — hand-written rules survive the round trip.
- zsh and fish shell integration (Open Question 3) — a change to `base.patch_bashrc`, not to
  this wizard.
- Running `bat cache --build` to pick up user themes (Open Question 4).
- Theme or syntax authoring (a non-goal).
