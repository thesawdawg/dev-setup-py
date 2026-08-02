# Specification: `devstuff configure ansible`

**Date:** 2026-08-02
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

`ansible.cfg` is the configuration file most likely to be copied from a tutorial that is
years out of date, because ansible-core keeps moving settings and **never tells you** when
one stops working. A key in a section ansible no longer reads parses fine, reports nothing,
and does nothing.

Everything below was measured against ansible-core 2.20 while building this:

| what you write                        | `ansible-config validate` | does it work? |
|---------------------------------------|---------------------------|---------------|
| `[ssh_connection]` + `pipelining`      | unknown section           | **no** — absent from `dump` entirely |
| `[defaults]` + `pipelining`            | fine                      | yes |
| `stdout_callback = yaml`               | fine                      | **no** — the plugin was removed |
| `pipelining = "True"` (quoted)         | fine                      | **inverted** — read as False |
| `inventory = "./inv"` (quoted)         | fine                      | **no** — a path containing quotes |
| `callback_result_format = yaml`        | **unknown key**           | **yes** — validate is wrong |
| any of the above, world-writable dir   | fine                      | **no** — the file is ignored wholesale |

`[ssh_connection] pipelining = True` is probably the most copy-pasted stanza in Ansible.
On ansible-core 2.20 it is inert. `stdout_callback = yaml` is the standard advice for
readable output; that callback was removed and superseded by `callback_result_format`.
And the setting that *does* work is the one ansible's own validator rejects.

The wizard's job is to write only settings this ansible actually reads, and to prove it.

**Success criteria**

- Every setting written is one this ansible reads, proved by comparing against
  `ansible-config dump --only-changed` rather than assumed from validation.
- A config inherited from an older project has its dead sections *reported*, not silently
  rewritten or silently kept.
- The world-writable-directory rule is surfaced before it wastes anyone's afternoon.
- Nothing written until confirmed; unmodelled sections and keys survive untouched.
- Zero new runtime dependencies (`configparser` is stdlib).

**Non-goals**

- Inventory, playbooks, roles or `requirements.yml`. This wizard configures ansible, not
  what ansible does.
- `ansible-navigator`, AWX or execution environments.
- Vault *contents*. The wizard configures where the password comes from; `ansible-vault`
  does the rest.
- Connection-plugin settings that are per-inventory rather than global (`ansible_ssh_args`
  and friends now live in inventory variables).

---

## 2. Functional Requirements

### Catalog

- **FR-1** Settings are data in `configure/ansible/model.py`, and every field — INI section,
  key, type, default, and the name it dumps under — was read from `ansible-config list`.
- **FR-2** `SECTIONS` lists only sections this ansible reads. `ssh_connection` is absent,
  and appears in `RETIRED_SECTIONS` with an explanation instead.
- **FR-3** `STDOUT_CALLBACKS` lists the three callbacks ansible-core ships. `yaml` and
  `debug` are deliberately absent — measured, they are not in `ansible-doc -t callback -l`.
- **FR-4** Seven presets: `project`, `fast`, `ci`, `vault`, `become`, `current`, `empty`.
- **FR-5** No preset may set a removed callback, and every preset value must be a valid
  choice for its setting. Both are tests.

### Detection

- **FR-6** Before asking anything the wizard reports: ansible's version, which config is
  currently in force, whether `ANSIBLE_CONFIG` overrides everything, the existing file and
  how many settings it sets, and what the directory contains (playbooks, inventory, roles,
  collections, a vault password file).
- **FR-7** The config in force is read from `ansible-config dump`'s `CONFIG_FILE()`, which
  is the answer after ansible's whole search order *and* the world-writable rule. It is not
  reimplemented.
- **FR-8** A world-writable working directory is reported as a warning, before and after
  saving. ansible ignores `./ansible.cfg` there entirely — the file exists, parses, and does
  nothing.
- **FR-9** The suggested config drops paths that do not exist here: a `roles_path` of
  `./roles` is not proposed in a directory with no `roles/`.
- **FR-10** The available callbacks come from `ansible-doc -t callback -l` at run time.
  Names there are fully qualified, so a short builtin name is resolved against
  `ansible.builtin.` before being looked up — otherwise every builtin looks unavailable.
- **FR-11** The check is silent when the plugin list could not be read.

### Generated config

- **FR-12** A setting equal to ansible's own default is omitted.
- **FR-13** Every setting is written into the section `ansible-config list` declares for it.
- **FR-14** Booleans are written `True`/`False` — what `ansible-config init` itself writes.
- **FR-15** **Values are never quoted.** Measured: a quoted path becomes a path containing
  quote characters, and a quoted boolean is read as `False` — so quoting a boolean silently
  inverts it.
- **FR-16** Sections and keys read from an existing file that the wizard does not model are
  carried through untouched, including retired sections, which are preserved and reported
  rather than deleted.
- **FR-17** With nothing set, the file is comments only — no section header. A bare
  `[defaults]` would parse back as `{"defaults": {}}` and break FR-18. Measured: a
  comments-only file is valid and is still reported as `CONFIG_FILE`.
- **FR-18** The emitted text must parse back to what `render.data()` describes.

### Verification

- **FR-19** `validate.verify()` runs `ansible-config validate` against a throwaway copy.
- **FR-20** It additionally runs `ansible-config dump --only-changed` and asserts **every
  setting written appears in it, sourced from our file**. This is the check that matters: a
  setting in a section this version does not read passes validation and is absent here.
- **FR-21** A plugin option is dumped under `dump -t <type>` and with a lowercase name, so
  the dump is queried per `Setting.dump_type` and both name spellings are matched.
- **FR-22** `ansible-config validate` reports a plugin option as an unknown key even though
  ansible honours it. Those specific complaints are discounted, the discount is stated in
  the check's detail, and the setting is separately proved to work by FR-20.
- **FR-23** A retired or unknown carried-over section is its own failed check, naming what
  replaced it.
- **FR-24** A machine without ansible still gets the model checks; the ansible checks report
  themselves skipped rather than failed.
- **FR-25** "Show what ansible actually reads from it" is a menu action, printing the dump
  and naming anything written but not read.
- **FR-26** A failed check at save time asks for confirmation; it never blocks the save.

### Saving

- **FR-27** An existing file is copied to `<name>.bak.<timestamp>` before being replaced.
- **FR-28** A config not written by this wizard shows a diff and asks.
- **FR-29** A named vault password file that does not exist prints the three commands to
  create it safely — write, `chmod 600`, add to `.gitignore`.
- **FR-30** `--output <path>` writes elsewhere.

---

## 3. Non-Functional Requirements

- **NFR-1** Zero new runtime dependencies.
- **NFR-2** Unit tests require neither ansible nor the network; binary-dependent tests are
  skipped when it is absent.
- **NFR-3** No import of the wizard at CLI start-up.
- **NFR-4** Every failure path in `detect.py` and `validate.py` returns an empty value or a
  failed `Check`.
- **NFR-5** `detect.py` writes nothing.

---

## 4. Open Questions

| # | Question | Status |
|---|----------|--------|
| 1 | Should the wizard configure fact caching backends? | **Resolved 2026-08-02 — no.** `fact_caching` selects a plugin whose own options (redis host, jsonfile path) are a second form. The cache *timeout* is offered; the backend is not. |
| 2 | Should `[colors]` be configurable? | **Resolved 2026-08-02 — no.** Sixteen settings that change nothing about behaviour, in a wizard whose value is behavioural correctness. |
| 3 | Should it write `requirements.yml` or scaffold `inventory/`? | **Resolved 2026-08-02 — no.** That is project scaffolding, not configuration. |
| 4 | Should more plugin options be modelled now `dump_type` exists? | **Open.** The mechanism generalises, but every one added inherits the `validate` false positive (FR-22), and a config that makes `ansible-config validate` fail in CI has a real cost. |
| 5 | Should the wizard offer to `chmod o-w` a world-writable directory? | **Open.** It is a one-line fix for a silent failure, but changing the permissions of a directory the user did not name is a larger step than writing a file into it. |

---

## 5. Findings That Changed the Design

- **`[ssh_connection]` is not a section ansible-core reads.** `pipelining` moved to
  `[defaults]`. Verified in both directions: under `[ssh_connection]` nothing appears in
  `ansible-config dump --only-changed`; under `[defaults]` it appears as
  `ANSIBLE_PIPELINING`. This is why `RETIRED_SECTIONS` exists and why the dump comparison,
  not validation, is the load-bearing check.
- **The `yaml` stdout callback was removed.** ansible-core 2.20 reports it as superseded by
  `result_format` on `ansible.builtin.default` "from ansible-core 2.13 onwards". The first
  draft of the `project` preset set `stdout_callback = yaml`; it passed every check and
  produced JSON. Corrected to `callback_result_format`, verified by watching a real
  `ansible ... -m ping` run print YAML.
- **`ansible-config validate` reports a working setting as an unknown key.** It knows only
  core settings, and `callback_result_format` is a callback-plugin option. So the validator
  is not authoritative in *either* direction — it misses dead settings and rejects live
  ones. Hence `Setting.dump_type`, the discounted complaint, and the warning telling the
  user what they will see.
- **A quoted boolean is read as `False`.** `pipelining = "True"` disables pipelining. This
  is why values are written raw and why the reader coerces the way ansible does rather than
  the way `configparser` would.
- **`ansible-config dump -t callback` names options in lowercase**, unlike the SHOUTING_CASE
  of core settings. Found when the reads-back check reported a working setting as unread.
- **ansible ignores `./ansible.cfg` in a world-writable directory.** It warns, then reports
  `CONFIG_FILE() = None`. Worth surfacing loudly: it is the one failure where the file is
  perfect and still does nothing.
