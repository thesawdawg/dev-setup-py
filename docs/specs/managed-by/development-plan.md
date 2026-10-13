# Managed-by notes — development plan

## Milestones

- **M-1 — data model.** `managed_by` catalog field + validation; `GenericTool`
  field; `tools.yaml` entries for python and pi. Covered by `tests/test_catalog.py`
  additions (accepted/rejected values, round-trip, bundled keys resolve).
- **M-2 — `managers.py`.** `ManagerNote`, `manager_of`, `note_for`, the type
  signal (shared `uv tool list` probe, new `_nvm_npm_globals` `_once` probe), the
  path signal (`_uv_roots`/`_nvm_roots` + `is_relative_to`), `list_line` and
  `picker_hint`. Covered by `tests/test_managers.py` — every state, both signals,
  probe failure, Rich escaping, never-raises.
- **M-3 — surfaces.** The note rides the existing worker pools in `list_cmd._probe`
  and `_install_interactive`; list appends the line after help/requires; the picker
  applies the suffix/disabled override. Covered by CliRunner list tests and a
  captured-picker-choices test.
- **M-4 — live check.** Real `devstuff list` / `list --installed` on the author's
  machine; a PTY-driven picker capture; timing before/after.

## Testing strategy

Probes are monkeypatched (`generic._uv_tool_versions`, `managers._nvm_npm_globals`,
`managers._ROOT_PROBES`, `shutil.which`, `generic._probe`) so each test reaches the
verdict it names; `generic.forget_probes()` isolates the `_once` memos. The
sibling-directory case asserts the naïve `startswith` *would* have matched while the
verdict is `outside`. The escaping test renders through a real `Console`, not a
string comparison. The nvm JSON probe is tested for both nonzero-rc-with-valid-JSON
and unparseable output.

## Risks

- A `managed_by` key with no root probe (any catalog key is valid) → `unknown`,
  covered by test.
- `note_for` must never break `list` → everything runs under a top-level `try`,
  covered by test.
- The path signal judges what `PATH` resolves, not what the tool's own check saw. If
  `python` is uv-managed (its check reads `~/.local/bin/python3` directly) but
  `~/.local/bin` is not on the `PATH` devstuff runs with, `python3` resolves to
  `/usr/bin` and the row reads `installed outside uv`. That is true of what the shell
  would run, so it is left as is rather than special-cased.
- Per-run cost → notes ride the existing probe pools; one `uv tool list`, one
  `npm ls -g`, ≤2 `uv * dir` calls per process via `_once`.

## What was run and what was not

Run on the author's machine, 2026-10-13:

- `uv run pytest tests/test_managers.py tests/test_catalog.py -q` — 62 passed.
- `uv run pytest -q` (full suite) — 2410 passed, 52 skipped, 71 deselected.
- `uv run ruff check .` — clean.
- Real `COLUMNS=160 uv run devstuff list` and `list --installed`, saved to
  `/tmp/managed-by-list.txt` and `/tmp/managed-by-list-installed.txt`. Measured
  states matched the host facts: ipython/hey-dave `✓ managed by uv`, commitizen
  `installed outside uv` (`.venv/bin/cz`), claude-code/codex/pi `✓ managed by nvm`,
  python `python3 at /usr/bin/python3.14 isn't managed by uv; …`, uninstalled
  uvx/npm rows `can manage this`.
- The interactive `install` picker driven under a real pty (200×120, arrow-key
  scroll, Ctrl-C to exit — nothing installed), captured to
  `/tmp/managed-by-picker.txt`: `installed · managed by uv|nvm`,
  `installed outside uv`, `(via uv)` / `(via nvm)` suffixes all rendered.
- Timing `uv run devstuff list` three times each way: before ~2.9s
  (2.98/2.85/2.96), after ~3.2s (3.30/3.23/3.15) — the ~0.3s is the four new
  per-process probes (`uv tool list`, `uv tool dir`, `uv python dir`,
  `npm ls -g`), each `_once`-memoised and shared across rows.

Not run: no tool was installed, removed or updated; no sudo/apt/npm/Docker on the
host. The notes' states on a machine *without* uv or nvm are covered only by the
monkeypatched unit tests, not a real manager-free host.
