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

### M2 — the update-path bug (FR-9, FR-10)

`_update_uvx` switches to `uv tool install "<pkg>==<ver>" --force` when a version is given, carrying
the FR-1..3 flags, and warns about the pin. Unpinned updates keep using `uv tool upgrade`.

This is a **pre-existing bug**, not a regression from M1 — `devstuff update <uvx-tool> --version X`
has never worked (F-5). Worth landing as its own commit with its own `fix:` message so it shows up
in the changelog independently.

**Exit:** a unit test asserting the argv shape for both the pinned and unpinned branches. The real
behaviour was already verified by hand against uv 0.11.21.

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

### M4 — uv-managed Python (FR-15..FR-17)

New `python` entry in `tools.yaml`. The risk here is entirely in `check_cmd` (FR-16, SD-8) — write
it against `uv python list --only-installed` / `uv python dir` rather than `command -v python3`, and
verify it answers *false* on a machine that has system Python but no uv-managed one.

**Exit:** added to the CI matrix; check verified false-then-true across an install.

### M5 — per-repo ansible helper (FR-18..FR-20)

New `functions.yaml` entry. Also update `src/dev_setup/functions.schema.json` if any field shape
changes — CLAUDE.md notes it is hand-maintained and drifts silently.

**Exit:** run it in three places — a uv ansible repo, a non-ansible repo, and `/tmp` — and confirm
exit 0 in all three (FR-19).

### M6 — docs

README "Built-in packages" tables reflect `ansible`/`ansible-vault` moving to `uvx` and the new
`python` entry. Update this spec directory with anything the implementation contradicted, per the
project's spec-currency rule.

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
- Unit suite green; `make run-tests TOOL=ansible` green.
- This spec directory matches what was built.
