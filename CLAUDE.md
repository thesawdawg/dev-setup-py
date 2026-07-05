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
legacy JSON at `~/.config/dev-setup/packages/*.json` is migrated in → user YAML at
`~/.config/dev-setup/tools.yaml` overrides matching keys in place and appends new ones.
`registry.py` turns that merged dict into `GenericTool` instances; a tool is `builtin` only if
it came from bundled and has no user override.

**Execution model**: `GenericTool.install()`/`remove()`/`is_installed()` branch on
`self.install_type` (`npm`, `pip`, `uvx`, `apt`, `git`, `script`, `bash`) and shell out via
`subprocess`. `install()` raises `RuntimeError`/`CalledProcessError` on failure — there's no
result enum, command handlers just catch and report. `bash`-type scripts are written to a temp
file and run with `bash <file>` for full parsing fidelity (not `bash -c "<string>"`).

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
