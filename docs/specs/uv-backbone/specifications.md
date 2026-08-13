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
- **F-12 (M4, 2026-08-12). `uv python install --default` creates `python`, `python3` *and*
  `python3.X` symlinks in uv's bin directory** (`~/.local/bin` by default). Since uv's own bashrc
  line puts that directory early on `PATH`, this changes what `python3` means in the user's
  interactive shells. `/usr/bin/python3` and anything with an explicit shebang are unaffected. The
  install script says so out loud rather than leaving it to be discovered.
- **F-13 (M4). `uv python uninstall --all` is too destructive to use for `devstuff remove`.**
  It removes *every* managed interpreter, including ones existing `uv tool` environments were built
  against — so removing the `python` tool could break `ansible` or `commitizen`. Removal instead
  resolves the default shim, reads its version, and uninstalls only that. Verified: with 3.14.6
  (default) and 3.12.13 installed, removal takes 3.14.6 and its three shims and leaves 3.12.13 and
  its `python3.12` shim intact.
- **F-14 (M4). A dangling shim must not read as installed.** This machine already carried a
  `~/.local/bin/python3.11` symlink pointing at a removed interpreter. `readlink -f` still prints a
  path under uv's python dir, so the check uses `test -x` (which follows the link and fails) rather
  than `test -e`. Verified against the real dangling link.
- **F-15 (M4). An empty `uv python dir` would make the check match everything.** With uv absent,
  `d=""` turns the `case` pattern `"$d"/*` into `/*`, which matches every absolute path — so a
  machine with no uv would report the tool installed. Hence the explicit `test -n "$d"` guard,
  verified with uv removed from `PATH`.
- **F-16 (M5). A trailing `[ -n "$x" ] && echo …` silently violates FR-19.** As the last statement
  of a branch it sets the script's exit status, so a correct "no global ansible found" answer
  exited 1 and surfaced as a red *'Which Ansible' failed* banner. Caught by running the case, not
  by reading the script. Fixed with full `if` blocks plus a closing `exit 0`.
- **F-10 (found during M2, 2026-08-12). `uv tool upgrade` is a no-op on a pinned tool, and the
  unpinned update path could not clear the pin.** Measured: after
  `uv tool install commitizen==4.16.0`, `uv tool upgrade commitizen` answers *"Nothing to
  upgrade"* forever. Since the original FR-9 kept `uv tool upgrade` for the unpinned path, a user
  who ever pinned a tool had **no route back to latest through devstuff at all**. Fixed by routing
  both paths through `uv tool install --force`, with `<pkg>@latest` for the unpinned case —
  measured to re-resolve *and* drop the specifier from the receipt in one call.
- **F-11 (2026-08-12). Two plausible remedies for a pin do not work, and were shipped in drafts
  of the warning text before being measured.**
  (a) *"Re-run without `--version`"* — false while that path was `uv tool upgrade`.
  (b) *"Run `devstuff install <key>` to clear the pin"* — false because `install_cmd.py:48`
  returns early on `is_installed()` and never reaches `uv tool install`. A bare
  `uv tool install <pkg>` *does* clear the specifier, but no devstuff command reaches it.
  The lesson is the project's own: a remedy printed to the user is a claim about the tool, and
  claims about the tool get measured.
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

- **FR-9 (revised 2026-08-12 during M2).** `_update_uvx` uses `uv tool install --force` on **both**
  paths — `<pip_name>==<version>` when pinned, `<pip_name>@latest` otherwise — never
  `uv tool upgrade`. The original requirement kept `uv tool upgrade` for the unpinned path; that
  was wrong, see F-10.
- **FR-10 (revised).** A pinned update warns that the pin holds until the next `devstuff update`
  without `--version`. The warning must state an escape route that actually works — see F-11 for
  two that do not.
- **FR-10a.** The FR-1..3 flags are passed on update as well as install, because `--force` writes
  a fresh receipt. Deliberately this also means a newly added `uv_with`/`uv_executables_from` in
  the catalog takes effect on the next update rather than only on a reinstall.

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
