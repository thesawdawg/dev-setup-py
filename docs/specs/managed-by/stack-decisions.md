# Managed-by notes — stack decisions

Decisions below were settled by the owner on 2026-10-13; rejected alternatives are
the part that gets re-litigated later, so they carry their reasons.

## Accepted

- **D-1 — `managed_by` field + derived defaults.** Explicit `managed_by: <key>` for
  tools whose type does not imply a manager (python, pi); `AUTO_MANAGER` derives
  `uvx`/`pip` → uv and `npm` → nvm so no YAML noise is added to those entries.
  `managed_by` names a *catalog key*, so `registry.exists` can drop typos and
  missing managers instead of printing wrong notes.
- **D-2 — Verify against the machine, not the catalog.** Installed rows are
  resolved through real probes (`uv tool list`, `npm ls -g` under nvm, executable
  paths vs manager-reported roots). Measured 2026-10-13 on the author's machine:
  `commitizen` reads installed via the repo venv's `cz` but is absent from
  `uv tool list` — catalog-only would claim it managed (outdated-spec finding F-2).
- **D-3 — Scope: uv and nvm only, plus system copies.** Only catalog managers get
  verdicts; `managed_by` to any other key answers `unknown`, not a guess. The one
  widening the owner asked for is `system-copy`: a not-installed tool whose
  executable exists outside the manager (python3 at `/usr/bin`) gets flagged
  because "install it through uv" is real advice there.
- **D-4 — Surfaces: `list` and the `install` picker.** Both filters
  (`--installed`/`--available`) included since the notes matter on both sides.
- **D-5 — The type signal reuses `outdated`'s probes.** `list` and `outdated` must
  agree about what uv manages, so `_uv_tool_versions` is shared verbatim rather
  than parsed a second way. nvm's signal mirrors it (`npm ls -g --json`) but lives
  in `managers.py` because the existing `_npm_global_installed` substring-matches
  per package and would query *system* npm when nvm is absent — a wrong-manager
  answer.
- **D-6 — `version_cmd` doubles as "the managed executable".** The path signal and
  `get_version` both need a real binary name, and `python`'s/`pi`'s `check_cmd`s
  are compound shell expressions that name none. `version_cmd: python3` /
  `version_cmd: pi` supply it — the same role the field already plays for
  `bwrap`/`fzf`. Side effect: `get_version` now reaches `python3 --version` /
  `pi --version` instead of falling through to `_bash_version(<key>)`.

## Rejected

- **Catalog-only notes** — would claim `commitizen` is uv-managed when it is not
  (see D-2). The whole feature exists because presence ≠ ownership.
- **Homebrew / other-manager suggestions** — noisy on machines without brew, and
  brew needs a formula-name map we do not have. Scope stays with managers the
  catalog itself can name.
- **Inferring management from `requires`** — `requires` is a prerequisite, not
  ownership: `ansible-vault` requires `ansible` but neither is managed by it.
  Conflating them would flag nearly every tool.
- **A "not managed" remedy on `outside` rows** — `install_cmd` early-returns on
  `is_installed()`; the advice would never run. `system-copy` carries
  `devstuff install <key>` only because there it does run.
- **`str.startswith` path containment** — `uv/python-other` shares the textual
  prefix of `uv/python`. `Path.is_relative_to` on resolved paths is required, and
  the sibling-directory test exists to prove it.
