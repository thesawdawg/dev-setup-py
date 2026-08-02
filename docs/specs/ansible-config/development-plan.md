# Development Plan: `devstuff configure ansible`

**Date:** 2026-08-02
**Status:** Milestones 1–5 complete

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Catalog entry | `ansible` in `src/dev_setup/tools.yaml` (`type: apt`) — already present | `devstuff install ansible` puts `ansible` on the PATH |
| 2 | Measured settings catalog | `configure/ansible/model.py` | Every section, key, type, default and dump name comes from `ansible-config list` |
| 3 | Emitter and parser | `configure/ansible/render.py` | Every preset round-trips; no value is quoted; an empty config is comments only |
| 4 | Checks | `configure/ansible/validate.py` | `validate` *and* the dump comparison, with plugin options queried under `-t` |
| 5 | Detection, wizard, wiring | `configure/ansible/{detect,wizard}.py`, registry entry, README, CLAUDE.md, this spec | The wizard reports, previews, checks, and saves with a backup |

## Testing Strategy

**`tests/test_configure_ansible.py` — unit by default, ansible-dependent tests skipped when
the binary is absent (NFR-2). 87 tests, 2 skipped:**

- **Model invariants:** every setting names a real section and group and maps to a field;
  `(section, key)` and dump names are unique; `Setting.default` equals the `AnsibleConfig`
  field default, since the emitter omits values equal to it.
- **The findings, asserted rather than commented:** `ssh_connection` is not in `SECTIONS` and
  is in `RETIRED_SECTIONS`; `pipelining` is a `[defaults]` setting; the removed `yaml` and
  `debug` callbacks are not offered and no preset sets them; a plugin option is marked as one
  and warns about the validator's false positive.
- **Rendering:** every preset round-trips; an empty config is comments only (the FR-17 bug,
  found by the round-trip check exactly as pre-commit's empty `repos:` was); booleans are
  `True`/`False`; **no value is quoted**; defaults are omitted; settings land in the section
  the binary declares; carried-over sections are written back and never overwrite a modelled
  key.
- **Parsing:** a broken file is reported rather than raised; a duplicate key is tolerated the
  way ansible tolerates it.
- **Reading back:** a hand-written config round-trips; unmodelled sections and keys land in
  `extra`; a retired section is *preserved*, not deleted; every ansible truthy spelling reads
  correctly; **a quoted boolean reads as `False`**, matching ansible rather than intuition.
- **Suggestion:** paths that do not exist here are dropped; ones that do are kept; an
  existing vault password file is picked up.
- **Checks:** a retired section is reported; an unknown section fails; a known carried
  section does not; `expected_env()` separates core from plugin settings; the callback check
  resolves short builtin names against `ansible.builtin.` and stays silent without a plugin
  list.
- **Against the real binary (skipped without ansible):** every preset passes; **every setting
  every preset writes appears in `ansible-config dump`**; the reads-back check is proved able
  to *fail* by monkeypatching `pipelining` back into `[ssh_connection]`; every section in
  `SECTIONS` is one this ansible knows; `ansible-config init --disabled` works; and real
  ansible rejects a retired section.

**Verified by hand, end to end** (a `pty.fork()`, as for the other wizards):

1. In a directory containing `site.yml`, `inventory/`, `roles/` and `.vault-pass`, the wizard
   reported ansible-core 2.20.1, 43 callback plugins, no config in force, and what it found
   in the directory.
2. The "Project defaults" preset proposed `./inventory`, `./roles` and `./collections` — and
   the review screen immediately flagged a bug in the first draft: **"'yaml' is not a
   callback this ansible can load"**. That warning is what uncovered the removed-callback
   finding.
3. After correcting the model, a config setting `forks`, `pipelining`,
   `callback_result_format` and `nocows` was written and handed to a real
   `ansible -m ping localhost` run, which **printed YAML** — the setting proved by its effect
   rather than by validation.
4. Saving reported "3 checks pass", including the dump comparison.

## Risks

| Risk | Mitigation |
|------|------------|
| **A future ansible moves a setting to another section.** Exactly what already happened to `pipelining`. | The dump comparison (SD-1) fails when a written setting is not read, so the move is caught the first time anyone runs the check rather than discovered months later. |
| **A future ansible removes a modelled setting.** | Same check: it would be written and not read back. A test also asserts every modelled section is one this ansible knows. |
| **The validator's false positive spreads to more settings.** | `Setting.dump_type` already generalises, and Open Question 4 records the cost of each addition rather than leaving it implicit. |
| **A user's inherited config carries a dead section.** | Preserved, and reported with what replaced it (SD-4). Never rewritten on their behalf. |
| **The wizard writes a config that is ignored entirely.** | The world-writable rule is checked and warned about before the questions and again after the save (FR-8). |
| **A vault password file is committed to git.** | The wizard warns, and prints the `chmod 600` and `.gitignore` commands when the named file does not exist yet (FR-29). |

## Not built

- Fact caching backends (Open Question 1) and `[colors]` (Open Question 2).
- Project scaffolding — `requirements.yml`, `inventory/` (Open Question 3).
- Further plugin options beyond `callback_result_format` (Open Question 4).
- Offering to `chmod o-w` a world-writable directory (Open Question 5).
- Migrating a retired section automatically — deliberately refused, see SD-4.
