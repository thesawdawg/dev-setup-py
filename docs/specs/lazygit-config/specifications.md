# Specification: `devstuff configure lazygit`

**Date:** 2026-08-02
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

lazygit has 273 configurable leaf settings and validates almost none of them. Measured
against 0.62.2, by starting it under a pty with each config:

| what you write               | lazygit's reaction                        |
|------------------------------|-------------------------------------------|
| a value of the wrong type    | **refuses to start**, and names the file  |
| unparseable YAML             | **refuses to start**                      |
| an **unknown key**           | starts, ignores it, says nothing          |
| an **invalid enum value**    | starts, falls back, says nothing          |

That asymmetry is the whole problem. A config assembled from blog posts — and lazygit
configs almost always are, because that is how people discover the delta integration —
starts perfectly and quietly does a fraction of what it says. `git.paging.useConfig`
appears in most delta guides and no longer exists. `nerdFontsVersion: "9"` is accepted and
draws no icons.

There is a second, more visible failure: turning icons on without a Nerd Font installed
fills the interface with boxes and question marks, and nothing connects the cause to the
effect.

**Success criteria**

- Every key written is one this lazygit actually reads, verified rather than assumed.
- Icons are gated on whether the font that draws them is installed.
- A user's `customCommands` and `keybinding` trees survive the wizard untouched — those
  are the parts of a lazygit config people have most invested in, and the wizard models
  neither.
- Nothing written until confirmed.
- Zero new runtime dependencies.

**Non-goals**

- Keybindings. A remapping UI is a different and much larger wizard, and an existing
  `keybinding` tree is preserved rather than edited.
- `customCommands`. Same reasoning: preserved, never authored.
- Themes beyond the icon settings. `gui.theme` is fifteen colour-role lists; the starship
  configurator is where this repo does colour work.
- Installing delta or a Nerd Font. The wizard points at `devstuff install`.

---

## 2. Functional Requirements

### Catalog

- **FR-1** Settings are data in `configure/lazygit/model.py`, each carrying the dotted path
  it writes to.
- **FR-2** **Every path was verified real by a type probe** (see SD-1): set the key to a
  value of obviously the wrong type and start lazygit. A real key produces an unmarshal
  error; an unknown key is ignored.
- **FR-3** `RETIRED_KEYS` records keys that appear in guides and no longer exist, with what
  replaced them. `git.paging.useConfig` is the one that matters.
- **FR-4** Seven presets: `recommended`, `plain`, `delta`, `minimal`, `careful`, `current`,
  `empty`.
- **FR-5** No preset may turn icons on without a font version, and every offered
  delta command must carry `--paging=never` — without it delta opens its own pager inside
  lazygit's, leaving a pane the user cannot escape. Both are tests.

### Detection

- **FR-6** The config directory comes from `lazygit --print-config-dir`, so the search
  order and the XDG variables are not reproduced.
- **FR-7** `lazygit --config` is read for *default values* and deliberately **not** as a
  list of valid keys — it omits every setting with no default, so `git.paging.pager` and
  the whole `os:` section are absent from it while being valid.
- **FR-8** `detect.default_drift()` compares the model's defaults against that dump and
  reports disagreements. The emitter omits a value equal to the modelled default, so drift
  means the wizard would stop writing a setting the user did choose.
- **FR-9** The Nerd Font check reuses the starship configurator's `fonts.detect()` rather
  than adding a second gate. It may answer `None` — "cannot tell" — which is not "no", and
  in that case the wizard stays silent and assumes a font is present.
- **FR-10** An existing config is read back into wizard state, and everything unmodelled —
  including whole `customCommands` and `keybinding` trees — is preserved verbatim.
- **FR-11** A value of the wrong type on disk is left alone rather than coerced. lazygit
  refuses to start on one, so the user has a real problem the wizard must not disguise.

### Generated config

- **FR-12** A setting equal to lazygit's default is omitted.
- **FR-13** The file carries comments explaining the settings that need explaining, so the
  emitter writes YAML by hand.
- **FR-14** A carried-over structure containing mappings is rendered with PyYAML and
  re-indented, rather than hand-emitted. `customCommands` is a list of mappings and getting
  it wrong loses the user's work.
- **FR-15** A scalar is written bare only if it provably round-trips through the parser,
  which is what keeps `nerdFontsVersion: "3"` a string and `expandAll: "="` a value at all.
- **FR-16** The emitted YAML must parse back to what `render.data()` describes.
- **FR-17** Reading any lazygit config goes through `render.load()`, a loader that resolves
  YAML's `=` value tag back to a string. lazygit's own default config contains
  `expandAll: =` and `yaml.safe_load` refuses the document outright.

### Verification

- **FR-18** `validate.verify()` runs offline before every save: the round-trip check, enum
  validation, retired keys, and default drift.
- **FR-19** Enum validation exists because lazygit does none — it accepts an invalid value
  and falls back silently.
- **FR-20** `validate.launch()` starts the real lazygit against the candidate in a
  throwaway git repository and quits it. It needs a pty (lazygit exits immediately when its
  output is a pipe, which would be indistinguishable from a rejected config) and a few
  seconds, so it is an explicit menu action.
- **FR-21** The launch check's own limits are stated to the user: starting successfully
  does not prove the keys work, because lazygit ignores the ones it does not know.
- **FR-22** A failed check at save time asks for confirmation; it never blocks the save.

### Saving

- **FR-23** An existing file is copied to `<name>.bak.<timestamp>` before being replaced.
- **FR-24** A config not written by this wizard shows a diff and asks.
- **FR-25** `--output <path>` writes elsewhere.

---

## 3. Non-Functional Requirements

- **NFR-1** Zero new runtime dependencies.
- **NFR-2** Unit tests require neither lazygit nor the network.
- **NFR-3** No import of the wizard at CLI start-up.
- **NFR-4** `verify()` is instant; only `launch()` costs seconds, and it says so.
- **NFR-5** Every failure path returns an empty value or a failed `Check`.

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should keybindings be configurable? | **Resolved 2026-08-02 — no.** A remapping UI over ~150 bindings is its own wizard. The existing tree is preserved. |
| 2 | Should `gui.theme` colours be offered? | **Resolved 2026-08-02 — not in v1.** Fifteen colour-role lists; the starship configurator is where this repo does colour, and lifting its palette model here is a larger change than it looks. |
| 3 | Should the type probe run at wizard time rather than authoring time? | **Resolved 2026-08-02 — no.** Each probe is a pty launch of ~2.5 seconds; probing 27 settings would take a minute and would need lazygit installed. Verified once, at authoring time, with the method recorded. |
| 4 | Should the wizard offer to install delta when it is selected but missing? | **Open.** `delta` is not currently in `tools.yaml`; adding it would make the same `install_cmd.install_by_key` offer the starship wizard makes for `nerd-font` possible. |
| 5 | Should `default_drift` fail a save or only warn? | **Open — currently a failed check, which asks.** It fires when lazygit changes a default, which is a real signal the model is stale, but it is not a problem with the user's config. |

---

## 5. Findings That Changed the Design

- **lazygit ignores unknown keys and rejects wrong types.** Everything in this package
  follows from that split: the tool cannot tell you a key is dead, so the wizard has to.
- **`lazygit --config` is not a list of valid keys.** It omits settings with no default.
  Trusting it would have made the wizard refuse to write `git.paging.pager` — the delta
  integration, which is the single most-wanted lazygit setting there is. Caught by probing
  a key the dump did not contain and finding it real.
- **The type probe.** Turning the type-strictness into a validity test is what made the key
  set verifiable at all. It is how `git.paging.useConfig` was found to be dead and
  `os.editPreset` alive.
- **`gui.sidePanelWidth` is a float, not a string.** Modelled wrongly at first; the
  default-drift check against `lazygit --config` caught it on the first run. That check
  earned its place immediately.
- **PyYAML cannot load lazygit's own default config.** `expandAll: =` — the binding for the
  equals key — hits YAML's `tag:yaml.org,2002:value`, and `safe_load` raises. Hence
  `render.load()` and the quoting of `=` on the way out.
- **A list of mappings must not be hand-emitted.** The first emitter stringified
  `customCommands` into `"{'key': 'C', ...}"`. Found by the test asserting a carried-over
  subtree survives — which is exactly the user data this wizard promises not to damage.
