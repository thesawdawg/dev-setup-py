# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`dev-setup` is a Python CLI (Click + Rich + questionary) that installs, removes, and tracks
developer tools on Linux. There is no per-tool Python code — every tool (built-in or
user-added) is a data record in a YAML catalog, executed by one generic engine
(`GenericTool` in `src/dev_setup/generic.py`). Adding a tool is a YAML edit, not a code change;
adding a new *install mechanism* (a "type") is a code change touched in ~5 places (see below).

## Commands

```bash
uv run dev-setup <cmd>       # run from source (repo root)
./dev-setup <cmd>            # bash wrapper — bootstraps .venv on first run, then execs Python
uv run pytest                # unit tests only (integration tests skipped by default, ~0.3s)
uv run pytest -m integration # real installs — requires sudo + network, run inside Docker (see below)
uv run pytest tests/test_catalog.py::test_user_catalog_overrides_bundled_tool_in_place  # single test
```

`pip` is not available in this dev environment — `uv pip install -e .` / `uv run` are the way in.
Interactive commands (`install`, `add`, `remove`, `delete`) open questionary/click prompts and
hang in non-TTY shells; don't try to drive them from a piped/non-interactive shell.

### Integration tests (real installs, Docker-isolated)

Integration tests actually install each builtin tool and assert `is_installed()` afterward. Run
via the Makefile in `dev/`, which builds the wheel and a throwaway CI image so nothing touches
the host:

```bash
cd dev
make run-tests                  # all builtin tools
make run-tests TOOL=uv          # just one tool's install test
make run-tests PYTEST_ARGS="-x --tb=long"
```

`tests/integration/test_tools.py` auto-parametrizes over every `builtin` registry entry except
those listed in its `_SKIP` dict (currently `docker`, `ollama` — can't run inside a container).
The `.github/workflows/test-installs.yml` weekly canary runs the same suite per-tool as a matrix
in fresh containers and files/updates a GitHub issue on failure.

### Releases

Versioning is Commitizen-driven (`[tool.commitizen]` in `pyproject.toml`, conventional commits,
`version_provider = "pep621"`). `.github/workflows/bump.yml` bumps the version and changelog on
merge to master; `publish.yml` ships to PyPI. Don't hand-edit the version in `pyproject.toml`.

## Architecture

```
src/dev_setup/
├── __main__.py     # python -m dev_setup entry point
├── cli.py          # Click group, command registration (see _register_commands)
├── base.py         # Tool ABC (is_installed/install/remove), WhichTool, bashrc patch helpers
├── catalog.py       # YAML load/validate/merge/import/export — the schema is enforced here
├── registry.py      # Loads the effective catalog into a live in-memory Tool registry
├── generic.py       # GenericTool — the ONE engine that implements every install type
├── tools.yaml       # Bundled built-in catalog (core/tools/languages categories)
├── ui.py            # Rich console + questionary wrappers (spinners, prompts, styled output)
└── commands/        # One Click command per file: list, install, remove, add, delete, docs, catalog
```

**Catalog precedence** (`catalog.load_effective_catalog`): bundled `tools.yaml` loads first →
user YAML at `~/.config/dev-setup/tools.yaml` overrides matching keys in place and appends
new ones. `registry.py` turns that merged dict into `GenericTool` instances; a tool is
`builtin` only if it came from bundled and has no user override.

**Execution model**: `GenericTool` is a dataclass; `install()`/`remove()`/`is_installed()`/
`update()` each look up `self.install_type` (`npm`, `pip`, `uvx`, `apt`, `git`, `script`,
`bash`) in a strategy-dispatch dict (`_INSTALLERS`/`_REMOVERS`/`_CHECKERS`/`_UPDATERS` in
`generic.py`) rather than an if/elif chain, and shell out via `subprocess`. `install()` raises
`RuntimeError`/`CalledProcessError` on failure — there's no result enum, command handlers just
catch and report. `bash`-type scripts are written to a temp file and run with `bash <file>` for
full parsing fidelity (not `bash -c "<string>"`). `dev-setup update` reuses the same dispatch
pattern for upgrading an already-installed tool (latest or a pinned version); for `script`/
`bash` types "update" is a full reinstall, since there's no narrower mechanism, so the command
layer confirms before re-running it.

**Two ways a tool gets defined**: built-in (an entry added directly to `src/dev_setup/tools.yaml`,
`builtin=True`) or custom (created via the `dev-setup add` wizard, `dev-setup catalog import`,
or hand-edited YAML, landing in the user catalog). Both use the identical schema — the only
difference is which file the key lives in and `category`.

## Adding a new built-in tool

Add an entry to `src/dev_setup/tools.yaml` using an existing `type` (`npm`, `pip`, `uvx`, `apt`,
`git`, `script`, `bash`) — see README.md "Custom packages → YAML schema" for the full field list
and per-type examples. Then:
- Add the key to `.github/workflows/test-installs.yml`'s matrix (or to `_SKIP` in
  `tests/integration/test_tools.py` with a reason, if it can't run in CI).
- Add it to the relevant table in README.md ("Built-in packages").
- No Python code changes needed — `GenericTool` already knows how to run every existing type.

## Adding a new tool *type* (e.g. a `composer`/PHP-package type)

`php` itself (the PHP runtime) is already a built-in `bash`-type tool — what's *not* supported is
installing PHP packages via Composer as their own first-class type (analogous to how `npm` and
`uvx`/`pip` are first-class today). Adding a type like `composer` touches every layer:

1. **`catalog.py`** — add any new field names (e.g. `composer_name`) to `SUPPORTED_FIELDS`, and
   if the type implies an auto-`requires` (like `npm` → `["nvm"]`, `pip`/`uvx` → `["uv"]`), add
   that inference in both `validate_catalog()` and `GenericTool.__init__`/`to_dict()` (`generic.py`)
   — these two must stay in sync or `to_dict()`'s "don't persist auto-inferred requires" logic
   will drift from validation.
2. **`generic.py` `GenericTool`** — add the new field(s) to `__init__`, `from_dict`, `to_dict`;
   add a branch in `is_installed()`, `install()`, `remove()`, and (if version detection needs
   type-specific logic) `_type_cmd()`.
3. **`commands/add_cmd.py`** — add the type to the wizard's type list (`install_type = ui.select(...)`)
   and add the type-specific prompt branch (mirrors the existing `npm`/`apt`/`git` branches).
4. **README.md** — add the type to the type table and the YAML schema field table, plus a
   worked example under "Custom packages → Examples".
5. **Tests** — extend `tests/test_catalog.py` for schema validation of the new fields; if you add
   a built-in tool using the new type, it's auto-picked-up by
   `tests/integration/test_tools.py`'s parametrization (add to CI matrix / `_SKIP` as above).

## Functions/scripts (a separate subsystem from tools)

`src/dev_setup/functions.yaml` + `functions_catalog.py` + `functions_registry.py` +
`function_runner.py` are a parallel, independent catalog/registry from tools — functions
aren't installed/removed, they're invoked (`dev-setup run <key>`), so they get their own
schema instead of overloading `GenericTool`. Some duplication with `catalog.py`/`registry.py`
is deliberate (see "Key design decisions" below).

`src/dev_setup/functions.schema.json` is a hand-maintained JSON Schema documenting every
field for editor tooling (YAML language server autocomplete/validation) — it is **not**
loaded or enforced at runtime (no `jsonschema` dependency), so if you add/change a field or
a constraint in `functions_catalog.py`'s `validate_catalog()`, update the schema file too or
they'll silently drift apart.

**Why two function `type`s exist**: a `dev-setup` command is its own child process, so
anything it does with `subprocess` (env vars, `cd`, aliases) is invisible to the shell that
invoked it the moment the process exits. `type: script` is for functions that don't need to
mutate the calling shell (runs as a subprocess, like a tool's `install_script`). `type:
shell-eval` is for functions that must (`ssh-agent`, `nvm use`-style tools) — it has two
`register` modes:
- `register: bashrc` (default) — `dev-setup functions enable <key>` patches a real shell
  function into `~/.bashrc` via `base.patch_bashrc`; the user calls it directly by name in a
  new shell afterward. `dev-setup run` refuses to run these directly (there's nothing it
  *can* do) and points at `functions enable` instead.
- `register: eval` — `dev-setup run <key>` prints resolved shell code to stdout for
  `eval "$(dev-setup run key args)"`. This path must never print anything else to stdout
  (no `ui.*` calls, no prompts) since it would corrupt what gets `eval`'d — missing required
  params are reported on stderr and exit non-zero instead of being prompted for.

**Named params, not positional**: catalog `params` entries become named shell vars in the
script body (`"$key_path"`, not `$1`). `function_runner.py` injects a prelude mapping real
argv positions to those names for `script`/bashrc-registered functions (`key_path="$1"`); for
`register: eval`, which has no argv channel of its own once `eval`'d, it instead bakes the
already-resolved values in as shell-quoted literals (`key_path='/path/with spaces'`).

**Gotcha if you touch `render_bashrc_function`**: it must strip blank lines from the function
body. `remove_bashrc_block` (shared with tool bashrc patches) treats the first blank line
after its marker as the end of the block, so a blank line inside the rendered function would
make `functions disable` orphan everything after it — closing brace included.

Functions have a `category` field (defaults to `custom`, freeform — not an enum) that
`functions list` groups/sorts by, mirroring tools. A `script`-type function that shells out to
another CLI should guard on `command -v <tool>` and point at `dev-setup install <tool>` in the
error rather than let a raw "command not found" surface — see `validate-yaml`/`aws-saml-reauth`
in `functions.yaml`. If that CLI is only reachable via nvm (like `pi`), source
`"$HOME/.nvm/nvm.sh"` first (see `acc-check`) — `script`-type functions run via a non-login,
non-interactive `bash <tmpfile>`, so `~/.bashrc`/nvm's shell init never runs on their own.

Not yet built: an `add` wizard and `catalog import`/`export` for functions, analogous to the
ones tools already have.

## Key design decisions (don't relitigate these)

- **uv owns Python provisioning.** The bash wrapper only guarantees `uv` is present; Python
  version and virtualenv management is delegated entirely to `uv run`.
- **Catalogs are the source of truth, not Python classes.** There is deliberately no per-tool
  subclass — everything is `GenericTool` driven by YAML data, so adding a tool is a data change.
- **`install()`/`remove()` raise, they don't return status codes.** No `InstallResult` enum;
  callers catch `RuntimeError`/`CalledProcessError`.
- **Invalid catalogs fail loudly at load time** — malformed YAML, unsupported version, unknown
  fields, bad `requires` all raise `CatalogError` immediately rather than silently degrading.
- **Custom install/remove scripts are plain strings**, written to a temp file at run time, so
  `bash` gets full script-parsing fidelity instead of `bash -c "..."` string quoting problems.
- **Functions get a parallel catalog/registry instead of extending `GenericTool`.** The
  schemas diverge enough (no `requires` inference, a `params` list, no install/remove
  lifecycle) that folding them into the tool catalog would be lossy; some duplication with
  `catalog.py`/`registry.py` is an accepted tradeoff over a forced shared abstraction.
