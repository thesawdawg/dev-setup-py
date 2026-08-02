# Development Plan: `devstuff configure lazygit`

**Date:** 2026-08-02
**Status:** Milestones 1–5 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Catalog entry | `lazygit` in `src/dev_setup/tools.yaml` (`type: bash`) — already present | `devstuff install lazygit` puts it on the PATH |
| 2 | Verified key set | `configure/lazygit/model.py` | Every dotted path was confirmed real by the type probe (SD-1), and `RETIRED_KEYS` holds what it found dead |
| 3 | Emitter and loader | `configure/lazygit/render.py` | Every preset round-trips; `customCommands` survives; `expandAll: =` reads and writes |
| 4 | Detection | `configure/lazygit/detect.py` | Defaults compared against the binary; unmodelled subtrees preserved; the font gate reused |
| 5 | Checks, wizard, wiring | `configure/lazygit/{validate,wizard}.py`, registry entry, README, CLAUDE.md, this spec | The wizard reports, previews, checks, launches lazygit on demand, and saves with a backup |

## Testing Strategy

**`tests/test_configure_lazygit.py` — unit by default, lazygit-dependent tests skipped when
the binary is absent (NFR-2). 69 tests:**

- **Model invariants:** every setting names a real group and field; paths are unique;
  `Setting.default` equals the `LazygitConfig` field default; no setting uses a retired key;
  every preset names real fields and valid choices.
- **The two preset safety rules, as tests:** no preset turns icons on without a font
  version, and every offered delta command carries `--paging=never`.
- **`sidePanelWidth` is a float** — asserted, because it was modelled as a string and the
  drift check caught it (SD-2).
- **Rendering:** every preset round-trips; an empty config is comments only; defaults are
  omitted; nested paths become nested YAML; `nerdFontsVersion: "3"` stays a string; a
  carried-over subtree is written back; a carried-over key never overwrites a modelled one.
- **The `=` keybinding**, four tests: that `yaml.safe_load` genuinely raises on it (so the
  reason `render.load` exists cannot silently evaporate); that `render.load` reads it as a
  string; that it comes back out quoted; and that broken YAML, an empty document and a
  non-mapping document are all reported rather than raised.
- **Reading back:** a config round-trips; **`customCommands` and `keybinding` survive
  untouched** while the consumed `gui` branch is dropped; a wrongly-typed value is left
  alone rather than coerced; a retired key is detected and *preserved*.
- **Suggestion:** icons avoided when the font is known absent, used when present, and used
  when the check answers `None` — "cannot tell" is not "no".
- **Checks:** an invalid enum value is caught (lazygit accepts it and falls back silently);
  a retired key is its own failed check; drift is empty without a dump; **a setting with no
  default in the dump is not drift**, which is the SD-1 mistake asserted against.
- **Against the real binary (skipped without lazygit):** the defaults dump is readable (so
  this also covers the `=` handling end to end); **the model does not disagree with this
  lazygit's defaults**; every preset passes the offline checks; real lazygit starts with the
  recommended config; **real lazygit refuses a wrongly-typed value**; and **real lazygit
  ignores an unknown key** — the two halves of the asymmetry the whole package is built
  around, asserted so that a future lazygit changing either is caught.

**Verified by hand, end to end** (a `pty.fork()`, as for the other wizards — and here the
wizard's own launch check uses one internally too):

1. The wizard reported lazygit 0.62.2, its config directory, and a Nerd Font present.
2. The "With delta" preset plus Nerd Font v3 and icons produced a config whose
   `git.paging.pager`, `gui.nerdFontsVersion`, `gui.showIcons` and `disableStartupPopups`
   all matched the choices, with per-setting comments.
3. Saving reported "3 checks pass".
4. Separately, a config carrying a realistic `customCommands` entry and an `expandAll: =`
   keybinding round-tripped exactly and was **accepted by a real lazygit launch**.
5. A config with a wrongly-typed value was rejected by real lazygit with its own error,
   confirming the launch check reports a genuine failure and not a timeout.

## Risks

| Risk | Mitigation |
|------|------------|
| **lazygit renames or removes a modelled key.** It would be silently ignored — the failure this wizard exists to prevent. | The key set is re-derivable: the probe method is recorded in `model.py`'s docstring rather than being a one-off. `RETIRED_KEYS` is where findings land. |
| **lazygit changes a default.** The emitter would stop writing a setting the user chose. | `default_drift` compares the model against `lazygit --config` on every check, and a test asserts no drift on the installed version (SD-2). |
| **A user's `customCommands` or keybindings are damaged.** The thing they have most invested in. | Preserved as opaque subtrees, rendered by PyYAML rather than by hand (SD-3), and asserted by a test — which is how the stringification bug was found. |
| **Icons enabled without a Nerd Font.** | Gated on the starship configurator's existing font check, including its "cannot tell" answer (SD-5). |
| **The launch check hangs.** lazygit is a full-screen TUI. | A hard `LAUNCH_TIMEOUT`, an early break as soon as a complaint appears, and `q` written to the pty regardless. Every path closes the fd and reaps the child. |
| **The launch check is mistaken for proof.** It only proves lazygit *loads* the file. | Stated in the confirmation text the user reads before running it (FR-21). |

## Not built

- Keybinding remapping (Open Question 1) and `gui.theme` colours (Open Question 2).
- `customCommands` authoring — preserved, never edited.
- Running the type probe at wizard time (Open Question 3).
- Offering to install delta when it is chosen but missing (Open Question 4) — `delta` is not
  in `tools.yaml` yet, which is what that would need first.
