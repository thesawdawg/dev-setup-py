# Specification: `devstuff configure starship`

**Date:** 2026-07-30
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

`devstuff install starship` installs the binary and adds `eval "$(starship init bash)"` to
`~/.bashrc`. That is where it stops: the user gets starship's stock prompt and a blank
`~/.config/starship.toml` that does not exist yet. Customising it means reading the module
reference, learning starship's format-string grammar (`[text]($style)`, `$module`, palettes,
powerline transitions), and iterating by editing a file and opening a new shell to see the
result.

`devstuff configure starship` replaces that loop with a wizard: pick a **style**, a **colour
palette**, which **sections** appear, and the **layout** — and see the actual prompt rendered
after every change, before anything is written to disk.

**Success criteria**
- A user who has never read starship's docs gets a prompt they chose, in under a minute.
- The preview is *real* — produced by the installed `starship` binary against the candidate
  config, not an approximation — whenever starship is installed.
- Nothing is written until the user confirms; an existing `starship.toml` is never lost.
- The generated TOML is a file a human can then hand-edit: commented, ordered, and using
  starship's own idioms rather than a devstuff-specific encoding.
- Zero new runtime dependencies.

**Non-goals**
- A general-purpose config-wizard DSL that other tools declare in `tools.yaml` (see SD-1).
- Round-tripping a hand-edited `starship.toml` back into wizard state (see SD-6).
- Shells other than bash for the `init` hook check (see FR-13).
- Exposing every starship module. The section list is a curated subset (see FR-4).
- A `--print`-to-stdout mode. The wizard is interactive and questionary drives stdout, so a
  config could never be cleanly piped out of it; `--output PATH` covers the real need
  (try a config without touching the live one).

## 2. Users & Personas

| Persona | Description | Primary needs |
|---------|-------------|---------------|
| New starship user | Just ran `devstuff install starship` | A good-looking prompt without reading the manual |
| Terminal tinkerer | Has a `starship.toml`, wants to try a different look | Preview alternatives safely; keep the old file |
| Font-constrained user | SSH/console without a Nerd Font | A style that renders in plain ASCII |

## 3. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-0 | Every generated config is accepted by `starship print-config` with no warnings, for every style × palette × layout combination. | Must |
| FR-1 | `devstuff configure` lists tools that have a configurator and their install state; with no argument in a terminal it offers a picker. | Must |
| FR-2 | `devstuff configure starship` runs a wizard covering: style preset, colour palette, sections, layout, and blank-line spacing. | Must |
| FR-3 | Three style presets — `plain` (ASCII, no icons), `icons` (Nerd Font glyphs), `powerline` (solid colour bars). Presets that need a Nerd Font say so at the point of choosing. | Must |
| FR-4 | Sections are a curated, grouped list (Context / Location / Git / Languages / Infrastructure / Shell) presented as a checkbox with sensible defaults pre-checked. | Must |
| FR-5 | Five palettes — `terminal` (inherits the terminal's ANSI theme) plus `catppuccin_mocha`, `nord`, `gruvbox_dark`, `tokyo_night`. Every palette defines the same semantic roles (`dir`, `git`, `lang`, `infra`, `shell`, `ok`, `err`, `muted`, `bar_text`). | Must |
| FR-6 | Three layouts — single line, two lines, two lines with the Shell-group sections right-aligned in `right_format`. | Must |
| FR-7 | After every change the wizard re-renders a **live preview** by writing the candidate TOML to a temp file and running `starship prompt` with `STARSHIP_CONFIG` pointed at it, inside a throwaway sample project (git repo + language marker files). | Must |
| FR-8 | When `starship` is not on `PATH`, or `starship prompt` fails, the wizard falls back to an offline approximation rendered from the same section/palette data, and says which one is being shown. | Must |
| FR-9 | A review menu loops: preview, then revisit any step, show the generated TOML, save, or cancel. | Must |
| FR-10 | Saving writes to `$STARSHIP_CONFIG` if set, else `~/.config/starship.toml`. An existing file is copied to `<name>.bak.<YYYYmmdd-HHMMSS>` first and the backup path is reported. | Must |
| FR-11 | The generated TOML carries a header comment naming the wizard, the chosen style/palette/layout, and a docs link. | Must |
| FR-12 | `--output PATH` writes the result there instead of the live config. `--path` prints the target config path and exits. `--list` lists configurable tools. | Should |
| FR-13 | After saving, if `~/.bashrc` has no starship init hook, the wizard offers to add one using the same `# Starship prompt` marker the installer uses, so `devstuff remove starship` still cleans it up. | Should |
| FR-14 | After a successful `devstuff install <tool>` for a tool that has a configurator, an interactive run offers to launch it. | Should |
| FR-15 | The generated TOML parses as valid TOML and every emitted format string is a TOML *literal* string, so starship's `$`/`[`/`\` grammar needs no escaping. | Must |
| FR-16 | Modules that starship disables by default (`kubernetes`, `time`) get an explicit `disabled = false` when selected. | Must |
| FR-17 | Cancelling — at any prompt (Ctrl-C) or via the menu — writes nothing and exits 0 with a note. | Must |

## 4. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | No new runtime dependencies. TOML is *emitted* as text (comments must survive) and validated with stdlib `tomllib` in tests. |
| NFR-2 | A preview render completes fast enough to feel live; the `starship prompt` subprocess is bounded by a 5 s timeout and never blocks the wizard on failure. |
| NFR-3 | The sample project and candidate config live in a temp directory removed on exit; the wizard never touches the user's real `starship.toml` before the save step. |
| NFR-4 | Preview failures are non-fatal by construction — every failure path returns `None` and the caller degrades to the offline renderer. |
| NFR-5 | Unit tests must run with starship absent, so all rendering logic is pure functions over the config model. |

## 5. Data Model

`StarshipConfig` (`configure/starship/model.py`) is the whole state the wizard edits:

| Field | Type | Meaning |
|-------|------|---------|
| `preset` | key into `PRESETS` | glyph set + whether backgrounds are drawn |
| `palette` | key into `PALETTES` | role → colour mapping emitted as `[palettes.<name>]` |
| `sections` | `list[str]` | selected starship module names |
| `layout` | `single` \| `two_line` \| `two_line_right` | where `$character` goes, and whether Shell sections move right |
| `blank_line` | `bool` | starship's `add_newline` |

`SECTIONS` is an ordered tuple of `Section` records — the canonical prompt order. Each carries
the starship module name, its wizard label and group, a palette role, the module `format` body
(e.g. `$symbol$branch`), a Nerd Font symbol, a plain-text symbol, a sample value for the offline
renderer, and any extra module keys (`truncation_length`, `min_time`, `disabled`, …).

Adding a section is one entry in that tuple — no other file changes.

## 6. Rendering Rules

- **Non-powerline:** module `style = 'fg:<role>'`, `format = '[<body>]($style) '`. The trailing
  space lives inside the module format so it disappears with the module.
- **Powerline:** module `style = 'fg:bar_text bg:<role>'`, `format = '[ <body> ]($style)'`. The
  top-level format opens with a `` cap, joins **runs of consecutive sections sharing a role**
  with `` transitions, and closes with a trailing ``. Grouping by run is what stops two
  adjacent language segments from drawing an arrow between two identical backgrounds.
- **Symbols are always emitted** for any section whose body references `$symbol`, including the
  empty string — otherwise starship's built-in glyph would leak into the `plain` preset. The
  converse is under test: a section carrying an icon its body cannot render is a bug, which is why
  `directory` (the one selectable module with no `symbol` key at all) has none.
- **The style key is per-section**, defaulting to `style`. `username` needs `style_user`, and sets
  `style_root` through `extra` so a root shell does not fall back to starship's off-palette red.
- `character` is not a section; its symbols come from the preset (`❯` / `$`) coloured `ok`/`err`.

## 6a. Verified findings that shaped the implementation

Each of these was found by running the real binary, and each is covered by a test or a comment:

| Finding | Consequence |
|---------|-------------|
| `username` rejects a plain `style` key; `battery` rejects both `style` and `symbol` (they live in its `[[battery.display]]` array). | Added the per-section `style_key`; **dropped `battery`** from the section list rather than carry three quirk fields for one module. |
| `kubernetes` and `time` ship disabled, so listing them in `format` is not enough. | FR-16. |
| With `STARSHIP_SHELL=bash`, starship wraps every escape in readline's `\[`/`\]` markers, which are invisible in a `PS1` but print literally when displayed. | The preview sets `STARSHIP_SHELL=nu`, the dialect that adds no wrappers. |
| starship takes the logical path from `PWD` when set, so the preview showed the user's real directory instead of the sample project. | The preview overrides `PWD`. |
| `right_format` is only rendered by `starship prompt --right`, so the right-prompt layout previewed as if the sections had vanished. | The preview makes a second call and Rich's grid right-aligns it on the cursor line. |
| starship's `right_format` needs a shell with a right-prompt mechanism; plain bash has none (ble.sh only). | The layout's description says "needs zsh, fish or nushell" at the point of choosing. |

## 7. Open Questions

| Question | Status |
|----------|--------|
| Should the sample project fake language *toolchains* as well as marker files? | **Resolved 2026-07-30 — no.** A language module needs the real binary to report a version, so a selected language renders in the live preview only on a machine that has it. Faking that would mean shimming executables onto `PATH` for a preview. The preview caption states the caveat instead; the offline renderer always shows every selected section. |
| In powerline mode, a run where no module renders (e.g. no language detected) leaves a stray transition arrow. Fix it? | **Resolved 2026-07-30 — accept.** Not expressible in starship's format grammar; the official `gruvbox-rainbow` preset has the same artifact. Grouping by role run (§6) reduces it to at most one per run. |
| Should `configure` be declared per-tool in `tools.yaml` (a `configure:` field)? | **Resolved 2026-07-30 — no.** See SD-2: a catalog field could name a configurator that does not exist, adding a validation failure mode for zero gain. The registry is keyed by tool key in Python. |
| Support fish/zsh in the FR-13 init-hook check? | Open. `base.patch_bashrc` is bash-only today; the check is skipped rather than guessed for other shells. |
| Offer `battery`? | **Resolved 2026-07-30 — no.** See §6a. It is the only module taking neither `style` nor `symbol`; supporting it means a `[[battery.display]]` array-of-tables emitter no other section would use. |
| Should the wizard offer starship's own official presets (`gruvbox-rainbow`, `pure`, …) as a starting point? | Open. `starship preset <name>` already does this, and the two would compete over the same file. Worth revisiting only if the section list stops being enough. |
