# Specification: `devstuff configure bat`

**Date:** 2026-08-02
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

`bat` is a `cat` with syntax highlighting, and almost everything that makes it worth
installing is off, invisible, or in the wrong place by default.

- It ships **28 themes** and no way to see one without typing its name. Choosing between
  them from a list of names is choosing blind.
- Its best feature is arguably not in its config file at all: `MANPAGER` gives you
  syntax-highlighted man pages, and nothing in `bat --help` suggests it.
- The config file is undiscoverable — `~/.config/bat/config`, created by nobody, in a format
  (one command-line option per line) that is not documented anywhere you would look first.

And it has a quiet failure of exactly the kind this repo's other configurators exist to
prevent. Measured:

| config                             | bat's reaction                                  | exit |
|------------------------------------|--------------------------------------------------|------|
| `--theme="NoSuchTheme"`            | `[bat warning]: Unknown theme ..., using default` | **0** |
| `--style="nosuchstyle"`            | `error: Unknown style` — refuses to run           | 1    |
| `--theme=Solarized (dark)` unquoted| tries to **open a file called `(dark)`**          | 1    |

The first is the dangerous one. A typo in a theme name produces a warning on stderr — which
a pager swallows — and bat carries on with the default theme forever. Nothing fails, so
nothing gets investigated; you just never get the theme you chose.

**Success criteria**

- Choosing a theme means *looking at it*: the candidate config renders a sample file through
  the real `bat` on every pass of the review loop.
- A theme name that this bat cannot load is caught before the config is saved.
- The shell integration (`MANPAGER`, `bathelp`, the `cat` alias) is offered, since it is half
  of bat's value and none of it lives in the config file.
- Nothing is written until the user confirms.
- Zero new runtime dependencies.

**Non-goals**

- Authoring themes or syntaxes. That is `bat cache --build` over a directory of `.tmTheme`
  and `.sublime-syntax` files — a different activity with a different lifecycle.
- `--map-syntax` rules. They are project-specific glob-to-language mappings; a hand-written
  one is preserved through the round trip but the wizard does not author them.
- Configuring the pager itself. `less` has its own flags and its own config.
- Shells other than bash. `base.patch_bashrc` is what the repo has.

---

## 2. Functional Requirements

### Catalog and presets

- **FR-1** Themes, style components, settings and presets are data in
  `configure/bat/model.py`. Adding one is a single record.
- **FR-2** Seven presets: `balanced`, `minimal`, `numbers`, `review`, `piping`, `current`
  and `empty`.
- **FR-3** No shipped preset may contain a conflicting component pair (FR-13). Asserted by
  `test_no_shipped_preset_contains_a_conflicting_component_pair`.
- **FR-4** The style components are those `bat --help` lists as components. The aggregates
  (`default`, `full`, `auto`, `plain`) are deliberately absent from the picker: the wizard
  always writes an explicit list so that what the config says is what you get.

### Themes

- **FR-5** The authoritative theme list is `bat --list-themes` at run time, not the shipped
  table — a user can add themes with `bat cache --build`, and a hardcoded list would then be
  wrong. A theme the shipped table does not know is offered, labelled "(yours)".
- **FR-6** The shipped table is the fallback for when bat cannot be asked, and carries the
  metadata bat does not expose: whether a theme suits a light or a dark terminal, or follows
  the terminal's own colours.
- **FR-7** Themes can be chosen as a light/dark *pair* under bat's `--theme=auto`, which is
  its default behaviour, or as one fixed theme.
- **FR-8** Every theme named in the config is checked against the live list before saving.
  This is the only thing that catches the silent-fallback failure.
- **FR-9** The theme check is silent when the live list could not be read — an unanswerable
  question must not become a false accusation.

### Generated config

- **FR-10** A setting equal to bat's own default is omitted. In particular `--theme=auto`
  alone is not written, and the default component set is not written as `--style`.
- **FR-11** Every string value is quoted. Measured: unquoted, a theme name containing a space
  is split into separate arguments and bat tries to open one of them as a file.
- **FR-12** Lines from an existing config that the wizard does not model are carried through
  to the output verbatim.
- **FR-13** Conflicting component pairs are warned about, not refused. Exactly one pair
  exists (`grid` + `rule`), found by sweeping all 21 pairs through the real binary.
- **FR-14** The emitted text must parse back to the flags `render.flags()` describes, checked
  by `render.matches()` for every preset.
- **FR-15** The file carries a generated-by header comment. bat's config format supports
  `#` comments, so unlike Docker's JSON it can identify its own output.

### Preview

- **FR-16** The preview is the real binary: the candidate config is written to a temp file,
  `BAT_CONFIG_PATH` points bat at it, and bat's own output for a sample file is shown
  verbatim, escapes and all.
- **FR-17** `--paging=never`, `--color=always` and `--decorations=always` are forced on the
  command line, which beats the config file — a preview must not open a pager inside the
  wizard, nor lose its colours for being piped.
- **FR-18** Any `BAT_*` variable in the user's environment is cleared for the preview. They
  beat the config file, so leaving them set would show their value rather than the
  candidate's.
- **FR-19** The same variables being set at all is reported as a warning on the review
  screen, since they will override the file being written.
- **FR-20** Without bat installed, the preview degrades to a description of the decorations —
  deliberately a description rather than an ASCII mock-up, since an approximation of a
  renderer is a thing that drifts from it.
- **FR-21** Every preview failure path returns `None` and degrades. A preview must never be
  able to end the wizard.

### Shell integration

- **FR-22** Four optional `~/.bashrc` additions are offered: `MANPAGER`, `MANROFFOPT`, a
  `bathelp` function, and the `cat` alias.
- **FR-23** No shell line may be blank or contain a blank line. `base.remove_bashrc_block`
  ends its block at the first blank line, so one would orphan everything after it on removal.
- **FR-24** Re-running the wizard removes its previous `~/.bashrc` block before writing the
  new one — `base.patch_bashrc` is a no-op when its marker is already present, so without
  this a second run would silently keep the old integration.
- **FR-25** The `cat` alias carries a caution: scripts still get the real `cat`, because
  aliases are interactive-only, but anything typed by hand now goes through bat.

### Saving

- **FR-26** An existing file is copied to `<name>.bak.<timestamp>` before being replaced.
- **FR-27** A config not written by this wizard shows a diff and asks before being replaced.
- **FR-28** `--output <path>` writes elsewhere and never touches `~/.bashrc`.

---

## 3. Non-Functional Requirements

- **NFR-1** Zero new runtime dependencies.
- **NFR-2** The unit test suite requires neither `bat` nor the network; tests needing the
  binary are skipped when it is absent.
- **NFR-3** No import of the wizard at CLI start-up.
- **NFR-4** A preview render completes in well under a second.
- **NFR-5** Every failure path in `detect.py` and `preview.py` returns an empty value or a
  failed `Check`.

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should the wizard author `--map-syntax` rules? | **Resolved 2026-08-02 — no.** A glob-to-language mapping is project-specific and belongs next to the project. Hand-written ones survive the round trip. |
| 2 | Should light/dark classification be computed rather than tabled? | **Resolved 2026-08-02 — tabled.** The measurement (mean foreground luminance) classifies 24 of 28 correctly and is *wrong* on the Solarized pair, which shares a palette by design. A table verified against the measurement beats a heuristic that is confidently wrong. |
| 3 | Should the wizard offer zsh/fish integration? | **Open.** `base.patch_bashrc` is bash-only and shared with the rest of the repo; multi-shell support is a change to that helper, not to this wizard. |
| 4 | Should `bat cache --build` be run when a user theme directory exists? | **Open.** It would make user themes appear in the picker, but it is a mutation of bat's state that a config wizard has no clear mandate for. |

---

## 5. Findings That Changed the Design

- **A bad theme is a warning and exit 0.** This is the reason FR-8 exists at all. A bad
  `--style` component, by contrast, is a hard error and needs no help from the wizard — so
  the checks are asymmetric on purpose.
- **Unquoted values containing spaces are split into arguments.** `--theme=Solarized (dark)`
  makes bat try to open `(dark)` as a file. Many shipped theme names contain spaces, so
  quoting is load-bearing rather than cosmetic (FR-11).
- **Classifying themes by foreground luminance gets Solarized wrong.** Both variants measure
  at ~130 because they share one palette — that is Solarized's entire design. Three more
  themes (`ansi`, `base16`, `base16-256`) emit no true colour at all and are neither light
  nor dark. Hence `Theme.mode` has three values, and the table is hand-corrected against the
  measurement rather than generated from it.
- **`rule` is a subset of `grid`.** Found by the live check rejecting the first draft of the
  `review` preset, which enabled every component. All 21 component pairs were then swept
  through the real binary: this is the only one that warns.
- **`bat --config-file` resolves the config path, honouring `BAT_CONFIG_PATH`.** Asked of the
  binary rather than reproducing the search order — the same instinct as mirroring
  `commitizen.config.read_cfg`, but without having to write the mirror.
