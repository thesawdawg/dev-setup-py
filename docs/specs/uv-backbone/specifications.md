# uv as the install backbone — specifications

Status: draft — 2026-08-12
Scope agreed with the maintainer on 2026-08-12: **engine + catalog**. Adding new uv-native
builtin tools (ansible-lint, molecule, ruff, yamllint …) is explicitly *out* of this scope and
recorded under "Deferred" below. The bias on borderline tools is **correctness first**: a tool is
only converted when the uv-installed artefact is the same tool or better.

## Problem statement

`devstuff` already has a `uvx` install type, but it can express exactly one thing:
`uv tool install <pip_name>`. That is not enough to install `ansible` correctly, and it is not
enough for uv to serve as the project's general install backbone.

The maintainer runs Ansible repositories that are themselves uv-managed (`pyproject.toml`,
`uv run ansible-playbook`). The devstuff-installed ansible is currently an **apt** package, which
is a differently-shaped artefact — a distro build against `/usr/bin/python3` — and therefore does
not compose with those repos.

## Measured findings

These were established against the real binaries on 2026-08-12 (uv 0.11.21, ansible 14.3.0 /
ansible-core 2.21.3). They are the reason the requirements below are shaped as they are.

- **F-1. `uv tool install ansible` installs exactly one executable: `ansible-community`.**
  Not `ansible`, not `ansible-playbook`, not `ansible-vault`. The `ansible` PyPI distribution is
  the collections bundle and declares no console scripts of its own; the ten real entry points
  belong to its `ansible-core` dependency, and `uv tool install` only exposes entry points of the
  *requested* distribution. A naive `type: uvx` + `pip_name: ansible` conversion therefore
  produces a tool that reports success and installs nothing usable.
- **F-2. `--with-executables-from ansible-core` is the fix.** Verified: it yields all eleven
  binaries (`ansible`, `ansible-config`, `ansible-console`, `ansible-doc`, `ansible-galaxy`,
  `ansible-inventory`, `ansible-playbook`, `ansible-pull`, `ansible-test`, `ansible-vault`, plus
  `ansible-community`).
- **F-3. uv persists install options in a receipt, and `uv tool upgrade` honours them.**
  `<UV_TOOL_DIR>/<tool>/uv-receipt.toml` records `requirements` (including everything passed via
  `--with` *and* `--with-executables-from`), the pinned `python`, and the entry-point→source-package
  map. Upgrades therefore do **not** lose these flags, so no extra bookkeeping is needed in
  devstuff. This is why the upgrade path is not a requirement here.
- **F-4. An `==` pin blocks future upgrades.** A tool installed as `ansible==14.2.0` answers
  `uv tool upgrade ansible` with *"Nothing to upgrade … installed with an exact version pin"*.
- **F-5. `_update_uvx` is broken for pinned versions.** It runs
  `uv tool upgrade "<pip_name>==<version>"`. `uv tool upgrade` takes a *tool name*, not a
  requirement; measured, it fails with `` `commitizen==4.16.0` is not installed ``. So
  `devstuff update <uvx-tool> --version X` cannot currently work for any uvx tool.
- **F-6. PyPI name collisions make a blanket conversion dangerous.** Measured on PyPI:
  `bat` is *Zeek Analysis Tools*; `lazygit` is an unrelated git-push helper; `git-lfs` is an
  unrelated fetcher; `yq` is Kislyuk's jq-wrapper, which has **different syntax** from the
  catalog's mikefarah Go `yq`; and `awscli` is still **v1** (1.46.0) — there is no AWS CLI v2 on
  PyPI. Converting any of these would silently install a different program.
- **F-9 (found during M1/M3 implementation, 2026-08-12). `_remove_uvx` ignored `remove_script`,
  unlike `_remove_apt`.** Harmless while `ansible-vault` was an `apt` entry; a live regression the
  moment it became `uvx`, because `devstuff remove ansible-vault` would then have run
  `uv tool uninstall ansible` and taken `ansible-playbook` and the rest with it. `_remove_uvx` now
  prefers an explicit `remove_script`, matching `_remove_apt`. Covered by
  `test_uvx_remove_prefers_remove_script`.
- **F-7. `_installed_uvx` is effectively dead code.** `GenericTool.is_installed`
  (`generic.py:147`) short-circuits on `check_cmd`, and every catalog entry sets one. The
  `_CHECKERS` entry is only reachable for a hand-written catalog entry with no `check_cmd`. Noted
  so a future reader does not "fix" a checker that never runs. Not a defect; no change required.
- **F-8. `uv python install --default` exists in 0.11.21** and installs `python`/`python3` shims
  into the uv bin directory. `uv python dir` reports `~/.local/share/uv/python`.

## Conversion list — every tool in `tools.yaml`, classified

28 catalog entries. **2 convert, 3 are already uv, 23 stay as they are.**

### Convert to `uvx` (2)

| Key | Today | Becomes | Why |
|---|---|---|---|
| `ansible` | `apt` | `uvx`, `pip_name: ansible`, `uv_executables_from: [ansible-core]` | The headline fix. Gives a uv-managed venv of the same shape as the maintainer's repos, a current ansible-core rather than the distro's, and a `~/.local/bin` install with no sudo. Requires F-2, hence the engine change. |
| `ansible-vault` | `apt` | `uvx`, same `pip_name`/`uv_executables_from` as `ansible` | `ansible-vault` is one of the eleven entry points from F-2, so it comes from the identical tool environment. Keeps its `requires: [ansible]` and its "bundled — remove ansible instead" `remove_script`. |

### Already `uvx` — no change (3)

`commitizen`, `ipython`, `pre-commit`.

### Stay as they are (23), with the reason

| Reason | Tools |
|---|---|
| Not Python; no PyPI distribution at all | `gh`, `go`, `mkcert`, `saml2aws`, `starship`, `htop`, `java`, `php`, `ruby`, `nerd-font`, `ollama`, `lmstudio`, `homebrew`, `docker` |
| Node ecosystem — npm/nvm is the correct backbone | `nvm`, `pi`, `llm-checker` |
| **PyPI name collision — a different program (F-6)** | `bat`, `lazygit`, `git-lfs`, `yq` |
| **PyPI has only the older major version (F-6)** | `aws` (PyPI `awscli` is v1; the catalog installs v2) |
| Bootstrap — cannot install uv with uv | `uv` |

`uv` deserves a note: an official `uv` wheel **does** exist on PyPI (0.12.3, measured). It is still
not usable here, because `uv` is the thing every `uvx` entry depends on; installing it through
itself is circular. The `bash` installer stays.

## Requirements

### Engine — new `uvx`/`pip` catalog fields

- **FR-1.** The catalog accepts `uv_with: list[str]`, mapped to one `--with <pkg>` per element.
- **FR-2.** The catalog accepts `uv_executables_from: list[str]`, mapped to one
  `--with-executables-from <pkg>` per element.
- **FR-3.** The catalog accepts `uv_python: str`, mapped to `--python <spec>`.
- **FR-4.** All three are rejected by `validate_catalog()` on any entry whose `type` is not `pip`
  or `uvx`, with a message naming the field and the offending type. A catalog that misuses them
  must fail at load time, consistent with the project's "invalid catalogs fail loudly" rule.
- **FR-5.** Extras need **no** new field. `pip_name` is passed as a single argv element to
  `subprocess`, never through a shell, so `pip_name: "ansible-lint[lock]"` already works. This is
  documented in the README schema table rather than implemented.
- **FR-6.** `GenericTool.__init__`, `from_dict` and `to_dict` round-trip all three fields, and
  `to_dict` omits them when empty so existing user catalogs are byte-identical after a rewrite.
- **FR-7.** `_install_uvx` builds its argv as
  `uv tool install [--python P] [--with W]… [--with-executables-from E]… <pip_name>`.
- **FR-8.** The `devstuff add` wizard prompts for these three fields when the chosen type is
  `uvx`/`pip`, mirroring the existing per-type prompt branches. All three are optional; empty
  answers must not write empty keys into the user catalog.

### Engine — update path

- **FR-9.** `_update_uvx` with an explicit `version` must use
  `uv tool install "<pip_name>==<version>" --force` (plus the FR-1..3 flags), **not**
  `uv tool upgrade`, which does not accept a requirement (F-5). Without a version it continues to
  use `uv tool upgrade <pip_name>`, which is correct and preserves the receipt's flags (F-3).
- **FR-10.** Pinning to an exact version must warn that the pin blocks later
  `devstuff update` runs until the tool is reinstalled unpinned (F-4).

### Ansible

- **FR-11.** Installing `ansible` provides all eleven executables of F-2 on `PATH`. This is the
  acceptance test for the whole change.
- **FR-12.** `ansible --version` reports its `python version` as the uv tool venv's interpreter,
  not `/usr/bin/python3`.
- **FR-13.** The existing `remove_script` for `ansible` (an `apt-get remove`) is deleted; the
  `uvx` remover (`uv tool uninstall`) handles it. The `ansible-vault` `remove_script`, which only
  prints guidance, is kept.
- **FR-14.** Installing devstuff's global ansible must not change what happens inside a uv-managed
  ansible repository: `uv run ansible-playbook` there continues to resolve to that project's own
  venv. The two are separate environments by construction; the requirement is that we add no
  `PATH` or `ANSIBLE_*` manipulation that would break it.

### uv-provisioned Python (new catalog entry)

- **FR-15.** A new builtin `python` entry of type `bash` installs a uv-managed CPython via
  `uv python install --default`, with `requires: [uv]`.
- **FR-16.** Its `check_cmd` must detect a **uv-managed** interpreter specifically, not merely any
  `python3` on `PATH` — every Linux host has one, so a naive check reports success before
  installing anything.
- **FR-17.** Removal uses `uv python uninstall`, and must state that the system `/usr/bin/python3`
  is untouched.

### Per-repo ansible helper (new `functions.yaml` entry)

- **FR-18.** A `script`-type function reports, for the current directory, **which** ansible would
  actually run: the project's uv venv if `pyproject.toml`/`uv.lock` declares ansible, otherwise the
  global uv tool install.
- **FR-19.** It exits **0** when it can answer, including when the answer is "no project ansible
  here" — a found-nothing result must not surface as a red failure banner. Non-zero is reserved for
  "could not perform the lookup". (Project rule; see `whats-on-port` in `functions.yaml`.)
- **FR-20.** It guards on `command -v uv` and points at `devstuff install uv` rather than letting
  `command not found` surface.

## Out of scope / deferred

- **New uv-native builtin tools.** `ansible-lint` (26.8.0), `molecule` (26.8.0),
  `ansible-navigator` (26.8.0), `yamllint` (1.38.0) and `ruff` (0.16.2) are all clean `uvx`
  candidates and all verified present on PyPI. Deliberately excluded from this scope; they are a
  pure catalog edit once FR-1..8 land.
- **Tightening `devstuff update` around `uv tool upgrade`** beyond the F-5 bug fix, which is
  in scope only because the feature is currently broken.
- `uv_constraints` / `--overrides` fields — no demand; YAGNI.
- Converting `uv` itself to its PyPI wheel (circular; see above).

## Open questions

- **OQ-1 (2026-08-12, open).** Should `ansible` carry a default `uv_with` list of the libraries
  ansible modules commonly need (`jmespath`, `netaddr`, `passlib`)? Argument for: they are the
  most frequent "module requires X" failures. Argument against: it bloats every install and the
  right place for them is the repo's own `pyproject.toml`. Recommendation: **no** by default;
  document `uv_with` in the README so a user can add them to their own catalog override.
- **OQ-2 (2026-08-12, open).** Should `uv_python` be set on `ansible`? Leaving it unset lets uv
  pick the newest interpreter, which was CPython 3.14 in testing. ansible-core 2.21 ran fine there,
  but ansible's support matrix trails new CPython releases. Recommendation: leave unset, revisit
  if a 3.14 incompatibility appears.
