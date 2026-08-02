# Stack Decisions: `devstuff configure lazygit`

**Date:** 2026-08-02
**Context:** The seventh configurator. SD-1 and SD-2 in `docs/specs/starship-config/` apply
unchanged. This is the only configurator whose tool provides *no* usable way to check a
config's keys, which is what most of the following is about.

---

## SD-1 — Key validity was established with a type probe, at authoring time

**Decision: every dotted path in `SETTINGS` was verified by setting it to a value of the
wrong type and starting lazygit under a pty. A real key produces an unmarshal error; an
unknown key is ignored and lazygit starts.**

lazygit rejects wrong types and silently ignores unknown keys. There is no `--validate`, no
schema shipped with the binary, and `lazygit --config` — the obvious candidate — is not a
key list: it omits every setting that has no default.

That last point was measured, and it mattered. `git.paging.pager` and the entire `os:`
section are absent from the defaults dump while being perfectly valid. A wizard that treated
the dump as authoritative would have refused to write the delta integration, which is the
single most common reason anyone opens a lazygit config at all.

The probe turns the one thing lazygit *does* check into the check it does not provide.

- **Rejected — trust `lazygit --config` as the key list.** Measured wrong, and wrong in the
  direction that breaks the most-wanted feature.
- **Rejected — vendor lazygit's JSON schema from schemastore.** A network dependency for a
  wizard whose core path must work offline (NFR-2), and a second copy of a thing that
  changes every release.
- **Rejected — run the probe at wizard time.** Each probe is a pty launch of roughly two
  and a half seconds; 27 settings is a minute of startup, and it would require lazygit to
  be installed for the wizard to work at all (Open Question 3).

**Consequence:** `model.py`'s docstring records the method so the table can be re-derived,
`RETIRED_KEYS` holds what the probe found dead, and `validate.launch()` exposes the same
mechanism to the user for the config as a whole.

## SD-2 — The defaults dump is used for values, and checked against the model

**Decision: `lazygit --config` is read for the *value* a setting defaults to, and
`detect.default_drift()` reports where the model disagrees.**

Having rejected the dump as a key list (SD-1), it would be easy to discard it entirely. But
it is authoritative about defaults, and defaults matter here more than they look: the
emitter omits any value equal to `Setting.default`, so a wrong default means the wizard
silently stops writing a setting the user explicitly chose.

The check earned its keep immediately — on its first run it caught `gui.sidePanelWidth`
being modelled as the string `"0.3333"` when lazygit's default is the float `0.3333`.

- **Rejected — hardcode defaults and never check.** The exact failure the check caught,
  shipped instead of found.
- **Rejected — read every default from the dump at run time instead of tabling them.** Then
  the wizard cannot work without lazygit installed, and the model stops being readable as a
  description of what the wizard does.

**Consequence:** a setting absent from the dump is explicitly *not* drift — absence means
"no default", not "disagreement". Getting that backwards would resurrect the SD-1 mistake.

## SD-3 — YAML is hand-emitted for the comments, except where a structure is carried over

**Decision: `to_yaml()` writes the file by hand so settings can carry explanatory comments;
a carried-over structure containing mappings is rendered with `yaml.safe_dump` and
re-indented.**

The hand-written emitter is the same decision as the pre-commit configurator's, for the same
reason — no YAML dumper emits interleaved comments, and a generated config people go on
editing needs them.

The exception is not a compromise, it is the correct split. The first version hand-emitted
everything and turned `customCommands` — a list of mappings — into the string
`"{'key': 'C', 'command': 'git cz'}"`. There are no comments to preserve inside a structure
the wizard did not author, so there is nothing to gain from hand-emitting it and a user's
custom commands to lose.

- **Rejected — hand-emit everything.** Shipped the bug once already; found by the test
  asserting carried-over subtrees survive.
- **Rejected — `yaml.safe_dump` for the whole file.** Loses every comment.
- **Rejected — ruamel.yaml.** A new runtime dependency on a globally installed CLI, rejected
  on the same grounds as in the pre-commit and agent specs.

**Consequence:** `_emit` handles scalars and scalar lists, and delegates anything else to
`_dump_block`. `matches()` covers both paths, which is what caught the bug.

## SD-4 — Reading a config needs a custom loader, and writing one needs matching care

**Decision: all parsing goes through `render.load()`, a `SafeLoader` subclass that resolves
`tag:yaml.org,2002:value` to a plain string; and the emitter quotes any value that would not
round-trip.**

lazygit's own default config contains `expandAll: =` — the keybinding for the equals key.
PyYAML maps a bare `=` to YAML 1.1's special "value" tag and `safe_load` raises
`ConstructorError` on the whole document. So without this, the wizard cannot read lazygit's
defaults *or* a user's config that binds that key.

- **Rejected — `yaml.safe_load` with a try/except.** Turns a legitimate config into an
  unreadable one; the user's `keybinding` tree would be lost rather than preserved.
- **Rejected — `yaml.unsafe_load` / `FullLoader`.** Solves it by allowing arbitrary tags in
  a file this wizard reads from disk. Never worth it.
- **Rejected — pre-process the text to quote bare `=`.** A regex over YAML, which is the
  usual way to invent a new bug.

**Consequence:** `_Loader` derives from `SafeLoader` and adds exactly one constructor, and
`_scalar()` asks the parser whether a value round-trips before writing it bare — so `=`
comes back out quoted.

## SD-5 — Icons are gated on the font, reusing the starship configurator's check

**Decision: `detect._nerd_font()` calls `configure.starship.fonts.detect()`, and the icon
questions are gated on the answer — including its `None`.**

Turning on `nerdFontsVersion` without the font installed fills lazygit with boxes and
question marks, and nothing in the interface connects the two. This repo already solved
exactly this problem for starship, including the parts that are easy to get wrong: the gate
is allowed to answer "don't know", it refuses to install over SSH because the glyphs are
drawn by the client's terminal, and it always says that installing a font does not repoint
the terminal at it.

- **Rejected — a second font check in this package.** Two implementations of a subtle
  detection that would drift, for no benefit.
- **Rejected — ignore the font and just offer icons.** The most common way a lazygit config
  looks broken.
- **Rejected — treat "cannot tell" as "no".** The starship spec is explicit that `None`
  means stay silent, and inverting that here would nag every user without fontconfig.

**Consequence:** `suggest()` picks the `plain` preset only when the font is *known* absent,
and `_ask_icons` warns and asks rather than refusing — the user may know something the
detector does not.
