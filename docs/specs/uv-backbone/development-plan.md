# uv as the install backbone — development plan

Companion to `specifications.md` and `stack-decisions.md`.

## Suggested first vertical slice

**FR-2 + FR-7 + the `ansible` catalog entry.** Adding just `uv_executables_from` and rewriting the
`ansible` entry is enough to prove the whole design end to end — it is the field with the most
mechanism behind it, and `ansible --version` reporting eleven binaries off a uv venv is an
unambiguous pass/fail. `uv_with` and `uv_python` are trivial once the argv-building shape exists.

## Milestones

### M1 — engine fields (FR-1..FR-8) — **done 2026-08-12**

Touching, per CLAUDE.md's five-place rule for schema changes:

1. `src/dev_setup/catalog.py` — add `uv_with`, `uv_executables_from`, `uv_python` to
   `SUPPORTED_FIELDS`; add the type-gate in `validate_catalog()` (FR-4). No `requires` inference
   changes — `uvx` already infers `["uv"]`, and that stays correct.
2. `src/dev_setup/generic.py` — the three fields in `GenericTool.__init__`, `from_dict`, `to_dict`
   (FR-6); argv building in `_install_uvx` (FR-7).
3. `src/dev_setup/commands/add_cmd.py` — three optional prompts in the `uvx`/`pip` branch (FR-8).
4. `README.md` — type table, YAML schema field table, and a worked ansible example.
5. `tests/test_catalog.py` — accepted on `uvx`, rejected on `apt`/`bash`, `to_dict` round-trip,
   and `to_dict` omitting them when empty.

**Exit:** `uv run pytest` green; a hand-written user catalog entry using all three fields loads.

### M2 — the update-path bug (FR-9, FR-10, FR-10a) — **done 2026-08-12**

`_update_uvx` routes **both** paths through `uv tool install --force` plus the FR-1..3 flags:
`<pkg>==<ver>` when pinned, `<pkg>@latest` otherwise.

The plan originally kept `uv tool upgrade` for the unpinned path; measuring it during
implementation showed that would have left a pinned tool permanently stuck (F-10), so the
requirement changed. Two draft warning texts named remedies that did not work (F-11) before the
current one was verified end to end.

This is a **pre-existing bug**, not a regression from M1 — `devstuff update <uvx-tool> --version X`
has never worked (F-5).

**Exit (met):** unit tests assert the argv shape for the pinned branch, the unpinned branch, flag
re-application, and the warning text. Verified against real uv: pin → 4.16.0 with a specifier in
the receipt; plain update → 4.17.0 with the specifier gone. Ansible keeps all eleven executables
across both a pinned and an unpinned update.

### M3 — ansible conversion (FR-11..FR-14) — **done 2026-08-12**

Rewrite `ansible` and `ansible-vault` in `tools.yaml` per the conversion table. Drop `ansible`'s
apt `remove_script` (FR-13). Keep `ansible-vault`'s.

`.github/workflows/test-installs.yml` — **this plan was wrong**: neither `ansible` nor
`ansible-vault` was in the matrix. Both added. Neither is in `_SKIP`, so the integration test
auto-parametrisation already covered them locally; only CI was missing them.

The integration assertion was strengthened as the risk table demanded: `_EXTRA_EXECUTABLES` in
`tests/integration/test_tools.py` now asserts `ansible-playbook`/`ansible-galaxy`/`ansible-vault`/
`ansible-doc` resolve on `PATH`, not just that `is_installed()` is true.

**Exit:** `cd dev && make run-tests TOOL=ansible` passes, and a manual check that all eleven
executables resolve and `ansible --version` reports the uv venv interpreter (FR-12).

### M4 — uv-managed Python (FR-15..FR-17) — **done 2026-08-12**

New `python` entry in `tools.yaml`. The risk here is entirely in `check_cmd` (FR-16, SD-8) — write
it against `uv python list --only-installed` / `uv python dir` rather than `command -v python3`, and
verify it answers *false* on a machine that has system Python but no uv-managed one.

**Exit (met):** added to the CI matrix. The check was verified false-then-true across an install,
and separately against the two ways it could lie: a dangling shim from a removed interpreter
(F-14, which this machine already had) and a missing uv collapsing the match pattern to `/*`
(F-15). Removal is targeted rather than `--all` (F-13).

### M5 — per-repo ansible helper (FR-18..FR-20) — **done 2026-08-12**

New `functions.yaml` entry. Also update `src/dev_setup/functions.schema.json` if any field shape
changes — CLAUDE.md notes it is hand-maintained and drifts silently.

**Exit (met):** run in five states — no uv (exit 1), bad path (exit 1), `/tmp` with no project
(0), this repo which is a uv project without ansible (0), and a real uv ansible repo before and
after `uv sync` (0). The first draft failed FR-19 exactly as predicted (F-16).

### M6 — docs — **done 2026-08-12**

Done as described, and the sweep turned up drift that predates this work:

- The `update` mechanism table still documented `uv tool upgrade` for `pip`/`uvx` (both columns).
- `eza` was deleted from the catalog in a1a5126 (2026-07-01) but never from the README table **or
  the CI matrix**, and `whichllm` was renamed to `llm-checker` in the same commit with the matrix
  never updated. A matrix entry naming a tool that no longer exists makes pytest exit 4 — so those
  two jobs have failed every weekly run since July, and the workflow's report step files a GitHub
  issue about them each time.
- Five real tools (`git-lfs`, `homebrew`, `ipython`, `llm-checker`, `lmstudio`) were missing from
  the README tables, and four (`bat`, `homebrew`, `llm-checker`, `lmstudio`) from the CI matrix.

All fixed, and `test_ci_matrix_covers_every_builtin_tool` now enforces the matrix half in both
directions — verified to fail on a stale entry *and* on a missing one. The README half is left to
manual review; its tables are prose-grouped and a parser would be more brittle than the drift it
catches.

## Testing strategy

- **Unit** (`uv run pytest`, ~0.3s) — schema acceptance/rejection, `to_dict` round-trip, and the
  argv built by `_install_uvx`/`_update_uvx`. The argv assertions are the ones that matter; they are
  what would catch a regression back to a bare `uv tool install`.
- **Integration** (`cd dev && make run-tests TOOL=ansible`) — real install in a throwaway container.
  The assertion should be strengthened beyond `is_installed()`: **assert `ansible-playbook` resolves**,
  not just `ansible`. F-1 is precisely a case where a weaker check passes on a broken install.
- **Manual, once** — FR-14: run `uv run ansible-playbook --version` inside a uv-managed ansible repo
  with the global tool installed, and confirm it still picks the project venv.

## Risks

| Risk | Mitigation |
|---|---|
| The integration test passes on a broken install because `is_installed()` only checks `ansible`. | Assert on `ansible-playbook` too. This is the exact shape of F-1 and the single most likely way this change ships broken. |
| uv changes `--with-executables-from` semantics or spelling. | Pinned only by uv's CLI stability. The failure is loud (unknown flag), not silent. Acceptable. |
| A user's existing catalog override for `ansible` keeps `type: apt`. | Catalog precedence means their override wins and they keep the old behaviour — correct, but worth a line in the README/changelog so it is not a mystery. |
| ansible-core lands on an interpreter it does not support (OQ-2). | `uv_python` exists specifically to pin it; leave unset until observed. |
| Removing the apt `remove_script` strands a previously apt-installed ansible. | Anyone who installed the old way has an apt ansible that `uv tool uninstall` will not remove. Mention in the changelog and in `devstuff docs ansible`; do not attempt an automatic migration — guessing at removing a system package is worse than saying so. |

## Definition of done

- `devstuff install ansible` puts all eleven executables on `PATH` from a uv-managed venv.
- `uv run ansible-playbook` inside a uv ansible repo is unaffected.
- `devstuff update commitizen --version 4.16.0` works (it currently cannot).
- All three new fields are documented in the README schema table and validated on the wrong types.
- Unit suite green. `make run-tests TOOL=ansible` **not yet run** — the containerised
  clean-install path remains unexercised for `ansible`, `ansible-vault` and `python`.
- This spec directory matches what was built.
