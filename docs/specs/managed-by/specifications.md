# Managed-by notes — specifications

> "When listing installable/installed tools, for any that are or can-be managed by
> another tool (i.e., uv can manage python) include an indication or recommendation
> note." — owner request, 2026-10-13

The motivating case was measured on the author's machine the same day: `commitizen`
shows as installed because `cz` resolves inside this repo's `.venv` under `uv run`,
yet `uv tool list` does not contain it — the catalog said "uvx tool", the machine
said "not managed by uv". A binary's presence alone cannot establish who owns it.

Owner decisions, 2026-10-13 (settled):

1. **Data**: a new optional catalog field `managed_by: <tool key>`, plus derived
   defaults (`uvx`/`pip` → uv, `npm` → nvm). No YAML edits for uvx/npm entries.
2. **Verify installed rows against the machine**; catalog metadata alone is not
   sufficient evidence of management.
3. **Scope**: only catalog managers (uv, nvm) — plus flagging *system copies* (a
   tool that reads not-installed while its executable exists elsewhere, e.g.
   `python3` at `/usr/bin`).
4. **Surfaces**: `devstuff list` (including `--installed`/`--available`) and the
   `devstuff install` interactive picker.

## Requirements

### Data model

- **FR-1** `managed_by` is an optional catalog field: a string matching the tool-key
  pattern, never the tool's own key. Validation rejects non-strings, non-keys and
  self-references; it does **not** check that the named manager exists in the same
  catalog file (a user catalog may point at a bundled key).
- **FR-2** `GenericTool` carries `managed_by` through `from_dict`/`to_dict`; empty
  is omitted in serialization.
- **FR-3** `AUTO_MANAGER = {"uvx": "uv", "pip": "uv", "npm": "nvm"}` supplies the
  manager when `managed_by` is unset. `manager_of` returns `None` when there is no
  manager, when the manager is the tool itself, or when `registry.exists(manager)`
  is false — a typo produces no note, never a wrong one.
- **FR-4** The bundled catalog sets `managed_by` exactly twice: `python` → `uv`
  (with `version_cmd: python3`, the managed executable) and `pi` → `nvm` (with
  `version_cmd: pi`).

### The verdict

- **FR-5** `note_for(tool, installed)` never raises and returns `None` or a
  `ManagerNote` in one of five states:

  | State | Meaning |
  |---|---|
  | `managed` | installed, and a probe confirmed the manager owns it |
  | `outside` | installed, and a probe confirmed the manager does not own it (path shown when resolvable); also: the manager itself is absent |
  | `can-manage` | not installed, and the manager could own it |
  | `system-copy` | not installed, but the tool's executable exists elsewhere |
  | `unknown` | a probe needed for the verdict failed — never rendered as managed |

- **FR-6** Type signal — uvx/pip tools under `uv`: membership of `tool.pip_name` in
  the shared `uv tool list` probe (`generic._uv_tool_versions`) is managed; absence
  is `outside`. A failed probe is `unknown` with the probe's `why`, except "uv is
  not installed", which is definitively `outside` — uv manages nothing it isn't
  there to run.
- **FR-7** Type signal — npm tools under `nvm`: membership of `tool.npm_name` in
  the output of `. "$NVM_DIR/nvm.sh" && npm ls -g --depth=0 --json` is managed;
  absence is `outside`. Valid JSON is parsed **regardless of exit code** (`npm ls -g`
  can exit non-zero over extraneous packages while printing a valid tree);
  unparseable output is `unknown`. No `nvm.sh` is `outside`.
- **FR-8** Path signal — explicit `managed_by` on other types: resolve the tool's
  executable (`version_cmd`, else simple `check_cmd`) and test
  `Path.is_relative_to` against the manager's own reported roots on resolved paths:
  `uv tool dir` + `uv python dir` for uv, `$NVM_DIR` (default `~/.nvm`) for nvm.
  Containment is `managed`; anything else is `outside`. A `startswith` check is
  forbidden — `uv/python-other` shares the prefix and is outside.
  - **FR-8a** nvm-managed executables that `shutil.which` misses get a second probe
    through an nvm-sourced `command -v` (pi's binary only exists once nvm puts a
    node bin dir on PATH).
  - **FR-8b** A manager whose roots probe fails yields `unknown`; a manager that is
    absent (no uv binary, no `$NVM_DIR`) yields `outside`. A `managed_by` key with
    no known roots (e.g. `docker`) yields `unknown`, not a crash.
- **FR-9** Uninstalled tools whose executable resolves on PATH are `system-copy`
  with `executable` and the resolved `path`; otherwise `can-manage`.

### Surfaces

- **FR-10** `devstuff list` appends a second description line per note state
  (`✓ managed by uv`, `installed outside uv (path); uv can manage it`,
  `⇢ uv can manage this`, `python3 at /usr/bin/python3.14 isn't managed by uv;
  \`devstuff install python\` lets uv manage it`, `? uv-managed tool; couldn't
  confirm (why)`), under all three filters. Machine-derived text is escaped
  through `rich.markup.escape`.
- **FR-11** The interactive `install` picker shows the same states: `managed`/
  `outside` replace the disabled text (`installed · managed by uv`), `can-manage`
  and `system-copy` append a plain-text title suffix (`(via uv)`,
  `(python3 found outside uv)`), and `unknown` keeps the default `installed` —
  never a positive claim. Disabled requires rows keep their `requires …` text and
  still get the suffix.
- **FR-12** All probes run inside the commands' existing worker pools; shared
  probes use `generic._once` so `forget_probes()` covers them and the 8-worker
  `list` runs each subprocess once.

## Non-goals

- No install remedy on `outside` rows — `install_cmd` returns early on
  `is_installed()`, so that advice would be false.
- No Homebrew/other-manager detection (see stack-decisions D-4).
- No migration or re-parenting of outside-installed tools.
- No `managed_by` enforcement at install time — the field annotates, it does not
  route installs through the manager.
