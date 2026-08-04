# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`devstuff` is a Python CLI (Click + Rich + questionary) that installs, removes, and tracks
developer tools on Linux. There is no per-tool Python code — every tool (built-in or
user-added) is a data record in a YAML catalog, executed by one generic engine
(`GenericTool` in `src/dev_setup/generic.py`). Adding a tool is a YAML edit, not a code change;
adding a new *install mechanism* (a "type") is a code change touched in ~5 places (see below).

## Commands

```bash
uv run devstuff <cmd>       # run from source (repo root)
./devstuff <cmd>            # bash wrapper — bootstraps .venv on first run, then execs Python
uv run pytest                # unit tests only (integration tests skipped by default, ~0.3s)
uv run pytest -m integration # real installs — requires sudo + network, run inside Docker (see below)
uv run pytest tests/test_catalog.py::test_user_catalog_overrides_bundled_tool_in_place  # single test
```

`pip` is not available in this dev environment — `uv pip install -e .` / `uv run` are the way in.
Interactive commands (`install`, `add`, `remove`, `delete`, `configure`) open questionary/click
prompts and hang in non-TTY shells; don't try to drive them from a piped/non-interactive shell.
To exercise one end-to-end anyway, fork a pty (`pty.fork()`) and write keystrokes to the fd —
that is how the starship wizard's full flow was verified.

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
├── configure/       # Per-tool setup wizards (see "Configurators" below)
└── commands/        # One Click command per file: list, install, remove, add, delete, docs, catalog
```

**Catalog precedence** (`catalog.load_effective_catalog`): bundled `tools.yaml` loads first →
user YAML at `~/.config/devstuff/tools.yaml` overrides matching keys in place and appends
new ones. `registry.py` turns that merged dict into `GenericTool` instances; a tool is
`builtin` only if it came from bundled and has no user override.

**Execution model**: `GenericTool` is a dataclass; `install()`/`remove()`/`is_installed()`/
`update()` each look up `self.install_type` (`npm`, `pip`, `uvx`, `apt`, `git`, `script`,
`bash`) in a strategy-dispatch dict (`_INSTALLERS`/`_REMOVERS`/`_CHECKERS`/`_UPDATERS` in
`generic.py`) rather than an if/elif chain, and shell out via `subprocess`. `install()` raises
`RuntimeError`/`CalledProcessError` on failure — there's no result enum, command handlers just
catch and report. `bash`-type scripts are written to a temp file and run with `bash <file>` for
full parsing fidelity (not `bash -c "<string>"`). `devstuff update` reuses the same dispatch
pattern for upgrading an already-installed tool (latest or a pinned version); for `script`/
`bash` types "update" is a full reinstall, since there's no narrower mechanism, so the command
layer confirms before re-running it.

**Two ways a tool gets defined**: built-in (an entry added directly to `src/dev_setup/tools.yaml`,
`builtin=True`) or custom (created via the `devstuff add` wizard, `devstuff catalog import`,
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
aren't installed/removed, they're invoked (`devstuff run <key>`), so they get their own
schema instead of overloading `GenericTool`. Some duplication with `catalog.py`/`registry.py`
is deliberate (see "Key design decisions" below).

`src/dev_setup/functions.schema.json` is a hand-maintained JSON Schema documenting every
field for editor tooling (YAML language server autocomplete/validation) — it is **not**
loaded or enforced at runtime (no `jsonschema` dependency), so if you add/change a field or
a constraint in `functions_catalog.py`'s `validate_catalog()`, update the schema file too or
they'll silently drift apart.

**Why two function `type`s exist**: a `devstuff` command is its own child process, so
anything it does with `subprocess` (env vars, `cd`, aliases) is invisible to the shell that
invoked it the moment the process exits. `type: script` is for functions that don't need to
mutate the calling shell (runs as a subprocess, like a tool's `install_script`). `type:
shell-eval` is for functions that must (`ssh-agent`, `nvm use`-style tools) — it has two
`register` modes:
- `register: bashrc` (default) — `devstuff functions enable <key>` patches a real shell
  function into `~/.bashrc` via `base.patch_bashrc`; the user calls it directly by name in a
  new shell afterward. `devstuff run` refuses to run these directly (there's nothing it
  *can* do) and points at `functions enable` instead.
- `register: eval` — `devstuff run <key>` prints resolved shell code to stdout for
  `eval "$(devstuff run key args)"`. This path must never print anything else to stdout
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
another CLI should guard on `command -v <tool>` and point at `devstuff install <tool>` in the
error rather than let a raw "command not found" surface — see `validate-yaml`/`aws-saml-reauth`
in `functions.yaml`. If that CLI is only reachable via nvm (like `pi`), source
`"$HOME/.nvm/nvm.sh"` first (see `acc-check`) — `script`-type functions run via a non-login,
non-interactive `bash <tmpfile>`, so `~/.bashrc`/nvm's shell init never runs on their own.

Not yet built: an `add` wizard and `catalog import`/`export` for functions, analogous to the
ones tools already have.

## Configurators (`configure/`) — tool-specific wizards, deliberately *not* catalog-driven

`src/dev_setup/configure/` holds per-tool setup wizards (`devstuff configure <tool>`), registered
in a `CONFIGURATORS` dict keyed by catalog tool key. There are seven:
`configure/starship/{model,render,preview,fonts,wizard}.py`,
`configure/commitizen/{model,render,detect,validate,wizard}.py`,
`configure/precommit/{model,render,detect,validate,wizard}.py` (the package can't be named
`pre-commit`, so the hyphenated catalog key maps to the `precommit` package in `CONFIGURATORS`),
`configure/docker/{model,render,detect,validate,wizard}.py`,
`configure/bat/{model,render,detect,preview,wizard}.py`,
`configure/ansible/{model,render,detect,validate,wizard}.py` and
`configure/lazygit/{model,render,detect,validate,wizard}.py`.

**The pattern they all share, and the one thing that matters most when adding another:**
*measure the tool, don't recall it.* Every configurator's `model.py` was built by interrogating
the installed binary, and in every single case that turned up something a plausible-sounding
memory would have got wrong — see each one's "things learned" block below. If you are adding a
configurator and you have not run the tool, you are guessing.

**The second shared pattern: work out what the tool's own validator does *not* check.** All four
of the newer wizards found their reason to exist there. `dockerd --validate`, `pre-commit
validate-config`, `ansible-config validate` and lazygit's startup check each accept configs that
are silently broken, and each in a different way. Establishing that boundary — by writing a
deliberately broken config and seeing what happens — is the first thing to do, not the last.

**Why this one breaks the YAML-catalog rule.** Installation generalises into ~7 mechanisms, which
is what makes `GenericTool` possible. Configuration does not: starship's config is a TOML file of
format strings, palette tables and powerline transition glyphs; another tool's would be shell
exports or `git config` calls. Expressing this wizard in YAML would need conditional questions, an
ordered section model with colour roles, and a template language — a programming language spelled
in YAML. So configurators are Python, dispatched from a dict, the same shape as `_INSTALLERS` in
`generic.py`. Full reasoning in `docs/specs/starship-config/stack-decisions.md` (SD-1).

A configurator that finds a missing *prerequisite* should offer to install it through
`install_cmd.install_by_key(key)` rather than telling the user to run a second command — that is
what the starship wizard does for `nerd-font`.

**Adding a configurator** — a new module plus one dict entry. The module must expose
`run(*, target: Path | None = None)` (returns `None` if the user cancelled; writes nothing until
they confirm) and `config_path() -> Path`. Everything generic — the picker, install-state check,
`--list`/`--path`/`--output`, and the post-install offer in `install_cmd.py` — reads the registry,
so none of it needs touching. There is deliberately **no `configure:` field in `tools.yaml`**: user
catalogs are strictly validated, so a catalog naming a nonexistent configurator would add a
load-time failure mode for nothing.

**Within the starship configurator, `model.py` is the data and everything else reads it.**
`SECTIONS` (ordered — declaration order *is* prompt order), `PALETTES` and `PRESETS` drive both the
TOML emitter and the offline preview, which is what stops those two from drifting. Adding a section
or palette is one entry; no other file changes.

**Things learned from the real binary — don't "simplify" these away:**
- `symbol` is emitted for every section whose body contains `$symbol`, *including as an empty
  string*. Omit it and starship's built-in Nerd Font glyph leaks into the `plain` preset. The
  converse is unit-tested: a section carrying an icon its body can't render is a bug (that's why
  `directory`, which has no `symbol` key at all, has no icon).
- Not every module spells the colour key `style` — hence `Section.style_key` (`username` wants
  `style_user`). `battery` is deliberately absent: it accepts neither `style` nor `symbol` (both
  live in its `[[battery.display]]` array), so it would cost three quirk fields for one section.
- `kubernetes`, `time`, `azure`, `status` and `shlvl` ship *disabled*; listing them in `format`
  isn't enough, they need `disabled = false`. `os` and `git_metrics` are absent for the same
  reason `battery` is: `os` takes its symbol from an `[os.symbols]` distro table, `git_metrics`
  has `added_style`/`deleted_style` instead of `style`.
- Format strings are emitted as TOML **literal** strings (`'…'`) so starship's `$`, `[`, and `\[`
  grammar needs no escaping. The multi-line top-level `format` is the one basic string, and it
  contains no backslashes of its own beyond the line continuations.
- Powerline transitions are emitted per **run of consecutive sections sharing a palette role**, not
  per section — otherwise two adjacent language segments draw an arrow between two identical
  backgrounds. The three powerline presets differ *only* in the four glyphs in their `Powerline`
  record (`POWERLINES` in `model.py`); nothing in the emitter knows which variant it is drawing.
- Any literal text the emitter writes into a format string is escaped with `_escape_format()`.
  This is not theoretical: `success_symbol = '[$](bold fg:ok)'` made starship read `$` as the start
  of a variable name, so the `plain` preset shipped with *no prompt symbol at all* until it was
  caught by sweeping every combination and asserting empty stderr.
- A custom module (`custom.compose`) is a normal `Section`, but its dotted key has to be written
  `${custom.compose}` in `format` — hence `Section.ref`. It also runs a shell command on **every**
  prompt, so it carries a `when` guard, and anything needing the network is disqualified.
- Symbols for new sections come from `starship print-config --default` — the module's own shipped
  glyph — rather than being picked from a Nerd Font chart that can't be verified without the font.
- The sample project must never gain a `bun.lock`: starship's `nodejs` module lists it as a
  *negative* detector, so it silently switches the Node section off in previews.
- The live preview runs `starship prompt` with `STARSHIP_SHELL=nu`, not `bash`: for bash, starship
  wraps escapes in readline's `\[`/`\]` markers, which are invisible inside a `PS1` and print
  literally anywhere else. It also overrides `PWD` (starship prefers it over the real cwd) and
  makes a second `--right` call, since `right_format` is not part of the left prompt.
- Every preview failure path returns `None` and degrades to the offline renderer. A preview must
  never be able to end the wizard.
- The Nerd Font gate (`configure/starship/fonts.py`) is allowed to answer "don't know": without
  fontconfig there is nothing to enumerate, and `None` means *stay silent* rather than warn. It
  also refuses to install over SSH — the glyphs are drawn by the client's terminal — and always
  says that installing a font does not repoint the terminal at it. The install itself goes through
  the ordinary catalog path (`nerd-font` in `tools.yaml` → `install_cmd.install_by_key`), never a
  private download (SD-10).

**Within the commitizen configurator, the object being configured is a list of commit types, not
a settings sheet.** `TYPES` in `model.py` is ordered (declaration order *is* prompt order, changelog
order and regex-alternation order), and `render.py` derives all nine `cz_customize` settings from
it — so adding a type is one `ChangeType` record and it reaches `bump_pattern`, `bump_map`,
`schema_pattern`, `change_type_map`, `change_type_order`, `commit_parser` and `questions` with no
other edit. Full reasoning in `docs/specs/commitizen-config/`.

**Things learned from the real binary — don't "simplify" these away:**
- `bump_map` is an **ordered** map and commitizen `break`s at the first key that `re.match`es, so
  the two breaking-change rules (`^.+!$`, `^BREAKING[\-\ ]CHANGE`) must be emitted first. Reorder
  them and `feat(api)!:` silently ships as a MINOR.
- What `bump_map`'s keys are matched against is **group 1 of `bump_pattern`** (`feat(api)!`), not
  the commit message. That is why `^.+!$` works at all, and why every selected type belongs in the
  pattern even when it has no map entry.
- `schema_pattern` must always accept the `bump:` prefix (`ALWAYS_ACCEPTED` in `model.py`):
  `cz bump` writes its own commit with that prefix, and `cz check --rev-range` over a release
  otherwise rejects commitizen's own commit. `cz_conventional_commits` accepts `bump` for exactly
  this reason without ever offering it in the picker.
- `commit_parser`'s trailing `|\w+!` alternative is load-bearing: it is what keeps `docs!: …` (a
  breaking change on a type with no changelog section) in the release notes at all. Verified — it
  lands in an unlabelled group rather than vanishing.
- The `BREAKING CHANGE` changelog heading only collects commits with a **footer**. A `feat!:` still
  bumps the major but is written up under Features, because that is the type it declared.
- Regexes are emitted as TOML **literal** strings (`'…'`) so no backslash needs doubling;
  `message_template` and `schema` are the exceptions (they carry real newlines, so they are basic
  strings). A user-supplied value containing a quote falls back to a basic string automatically.
- `config_path()` mirrors `commitizen.config.read_cfg`: search order *and* the rule that a file
  without a `commitizen` section doesn't count — otherwise every Python project on disk looks
  already-configured because it has a `pyproject.toml`.
- The `pyproject.toml` splice is line-based, so it verifies itself by parsing the result back and
  comparing the settings; a mismatch returns `None` and the caller writes `.cz.toml` instead. Don't
  replace that check with reasoning about which files it can handle.
- `validate.py` is the "measured, not assumed" half: it replays commits through the real
  `cz bump --dry-run` in a throwaway repo. It runs on an explicit menu action plus once at save
  time — not on every redraw (~3s, unlike starship's millisecond preview) — and a disagreement
  *warns*, it never vetoes a save. Keep that distinction in the comments.

**Within the pre-commit configurator, the object being configured is a curated catalog of hooks,
not arbitrary YAML.** `REPOS`/`HOOKS`/`PRESETS` in `model.py` are ordered and everything else reads
them — adding a hook is one `Hook` record and it reaches the picker, any preset naming it, the
language detector, the emitter and the preview with no other edit. Full reasoning in
`docs/specs/precommit-config/`.

**Things learned from the real binary — don't "simplify" these away:**
- **`default_install_hook_types` is derived from the selected hooks' stages, never asked.**
  `pre-commit install` installs *only* the `pre-commit` git hook unless given `--install-hook-types`,
  so a `commit-msg` hook (commitizen's) without this yields a config that validates, an install
  that reports success, and a hook that never fires. `install_hook_types()` feeds three places: the
  emitted key, the `pre-commit install --hook-type` flags, and a check in `verify()`. Verified end
  to end — with it, `git commit -m "oops no convention"` is rejected.
- **`pre-commit validate-config` does not check hook ids.** It reads the file's shape and never
  opens a repository, so a mistyped id passes and fails on the user's next commit. That is why
  `validate.resolve()` (`pre-commit install-hooks`, network, minutes) exists as a separate level
  from `verify()` (offline, milliseconds, runs before every save). Don't collapse them.
- **Quoting asks the YAML parser rather than a rules table** (`render._str`): a scalar is written
  bare only if `yaml.safe_load(value) == value`. This is what keeps shfmt's indent width `'2'` a
  string, which pre-commit's schema requires, and a rev like `1.0` from becoming a float.
- **`repos:` with nothing under it parses as null, not `[]`,** and pre-commit rejects it — the
  emitter special-cases `repos: []`. Found by the round-trip assertion, not by reasoning.
- **The emitter writes YAML by hand for the comments, and `render.matches()` parses it back and
  compares against `render.data()`.** Those two representations must stay in sync; the round-trip
  test over every preset is what enforces it. Don't swap in `yaml.safe_dump` (loses the comments)
  or ruamel (a new runtime dependency for one wizard).
- **`pre-commit autoupdate` can move a pin to a prerelease** (observed: isort `8.0.1` → `9.0.0b1`),
  so "refresh the revs" shows a diff and confirms rather than applying silently. The GitHub tags
  API is *not* version-ordered and disagreed with autoupdate in both directions — every rev in
  `REPOS` is what autoupdate itself resolved.
- **`ruff-check` carries `--exit-non-zero-on-fix` deliberately.** Without it the hook exits 0
  having rewritten files, and the fixes silently miss the commit that triggered them.
- **`Hook.needs` is load-bearing, not documentation.** It surfaces prerequisites on the review
  screen *and* derives the pre-commit.ci `skip` list — pre-commit.ci has no Docker daemon, so
  every `language: docker` hook must be skipped there or the whole CI run fails.
- **Language detection goes through `git ls-files`** so gitignored content can't decide the
  answer, and needs two files before a language counts — one stray `.sh` in a Python repo must not
  pull in two Docker-backed hooks.
- **The commitizen hook is only suggested when a commitizen config exists.** It runs `cz check`,
  which fails *every* commit without one.
- Conflicting hook pairs (`ruff-format` + `black`) are **warned about, not refused** — but no
  shipped preset may contain one, which is a test.

**Within the docker configurator, the object being configured is a daemon that has to be
restarted before anything takes effect.** Full reasoning in `docs/specs/docker-config/`.

**Things learned from the real binary — don't "simplify" these away:**
- **`dockerd --validate` accepts five configs that break every container.** Measured: an
  unloadable log driver, an unknown log option for the chosen driver, `compress: "true"` with
  `max-file: "1"`, an address pool `size` below its base prefix, and `hosts` alongside a systemd
  unit passing `-H`. The daemon starts healthy and every `docker run` then fails with an error
  that never mentions daemon.json. `validate.py` runs `--validate` *and* those checks; do not
  collapse it into "the tool already validates this".
- **Per-driver `log-opts` are measured, and two contradict the obvious guess.** `local` *does*
  take `tag`/`labels`/`env`; `journald` does *not* take `max-size`/`max-file`/`compress`. So
  `log_opts()` filters by driver rather than emitting whatever was set.
- **`none` is not in `docker info`'s `Plugins.Log`** — it is built into the daemon. The plugin
  list is "drivers that certainly exist", not "all valid drivers", hence `BUILTIN_DRIVERS`.
- **Only non-default values are written.** A config restating a default freezes it across
  daemon upgrades. This makes `Setting.default` and the `DockerConfig` field default a pair that
  must agree — which is a test, not a convention.
- **daemon.json has no comment syntax**, so it cannot carry a "generated by" marker (an unknown
  key is rejected — measured). The overwrite path shows a unified diff instead, which answers a
  better question anyway.
- **The restart is a separate confirmation and reads the daemon's *current* live-restore state**,
  not the new config's: turning live-restore on protects the *next* restart, not the one applying
  it.
- Writes go through `sudo install` staged via a temp file, never a redirect — a truncating
  redirect that fails leaves the daemon with half a config.

**Within the bat configurator, the preview is the deliverable** — it is the closest sibling to
starship's. Full reasoning in `docs/specs/bat-config/`.

**Things learned from the real binary — don't "simplify" these away:**
- **A bad theme is a warning and exit 0.** bat prints `[bat warning]: Unknown theme` to stderr —
  which a pager swallows — and carries on with the default forever. Every theme named is checked
  against `bat --list-themes`. A bad `--style` component, by contrast, is a hard error and needs
  no help; the checks are asymmetric on purpose.
- **The theme list is read at run time, not tabled.** `bat cache --build` lets users add their
  own. The shipped table is the offline fallback plus the light/dark metadata bat does not
  expose — and a test asserts every shipped name still exists, so the fallback can't rot.
- **Classifying themes by foreground luminance is wrong for Solarized.** Both variants share one
  palette by design and measure identically; three more (`ansi`, `base16`, `base16-256`) emit no
  true colour at all. Hence three modes, hand-corrected against the measurement.
- **Unquoted values containing spaces are split into arguments** — `--theme=Solarized (dark)`
  makes bat try to open a file called `(dark)`. Every value is quoted.
- **`rule` is a subset of `grid`** and bat says so. Found when the live check rejected a
  "turn everything on" preset; all 21 component pairs were then swept and this is the only one.
- The preview clears every `BAT_*` variable, because they beat the config file — otherwise it
  would render the environment's theme while claiming to show the candidate's.

**Within the ansible configurator, the check that matters is "did ansible *read* this",
not "is this valid".** Full reasoning in `docs/specs/ansible-config/`.

**Things learned from the real binary — don't "simplify" these away:**
- **`[ssh_connection]` is not a section ansible-core reads.** `pipelining` moved to `[defaults]`.
  Verified both ways: under `[ssh_connection]` nothing appears in `ansible-config dump
  --only-changed`; under `[defaults]` it appears as `ANSIBLE_PIPELINING`. That stanza is the most
  copy-pasted three lines in Ansible and it is inert.
- **So the load-bearing check is the dump comparison**, not `ansible-config validate`. Every
  setting written must appear in `ansible-config dump --only-changed` sourced from our file. A
  test proves the check can *fail* by moving `pipelining` back into `[ssh_connection]`.
- **The `yaml` stdout callback was removed** — superseded by `callback_result_format`. Setting
  `stdout_callback = yaml` passes every check and produces JSON. The first draft of the `project`
  preset did exactly this.
- **`ansible-config validate` rejects a working setting.** `callback_result_format` is a
  callback-*plugin* option and the validator only knows core settings. So it is unreliable in
  both directions. Hence `Setting.dump_type`, the discounted complaint (stated in the check's own
  detail, never silent), and the warning telling the user what they will see.
- **Plugin options dump under `-t <type>` and in lowercase**, unlike core settings' SHOUTING_CASE.
- **Values are never quoted.** `inventory = "./inv"` becomes a path containing quotes, and
  `pipelining = "True"` is read as **False** — quoting a boolean inverts it. The reader coerces
  the way ansible does, not the way `configparser` would.
- **A retired section is preserved and reported, never migrated.** The wizard cannot know whether
  an older ansible elsewhere still reads it.
- **ansible ignores `./ansible.cfg` in a world-writable directory** — warned about before the
  questions and again after the save.

**Within the lazygit configurator, the tool provides no usable way to check a config's keys.**
Full reasoning in `docs/specs/lazygit-config/`.

**Things learned from the real binary — don't "simplify" these away:**
- **lazygit rejects wrong *types* and silently ignores unknown *keys* and invalid enum values.**
  Everything in the package follows from that split. Two live tests assert both halves, so a
  future lazygit changing either is caught.
- **`lazygit --config` is not a key list.** It omits every setting with no default, including
  `git.paging.pager` — the delta integration everyone wants. Treating it as authoritative would
  have made the wizard refuse to write the most-wanted setting there is.
- **Keys were verified with a type probe**: set the key to a value of obviously the wrong type
  and start lazygit; a real key errors, an unknown one is ignored. The method is recorded in
  `model.py` so the table can be re-derived. It is what found `git.paging.useConfig` dead.
- **The defaults dump *is* used for values**, and `default_drift()` compares it against the
  model — which caught `gui.sidePanelWidth` being modelled as a string when it is a float. A
  setting absent from the dump is explicitly *not* drift.
- **PyYAML cannot load lazygit's own default config.** `expandAll: =` hits YAML's
  `tag:yaml.org,2002:value` and `safe_load` raises. All parsing goes through `render.load()`, and
  `=` is quoted on the way out.
- **A list of mappings must not be hand-emitted.** The first emitter stringified
  `customCommands` — the user data this wizard most promises not to damage. Carried-over
  structures go through `yaml.safe_dump` and are re-indented; only settings the wizard authors
  are hand-emitted, because only those need comments.
- **Icons reuse `configure/starship/fonts.detect()`**, including its `None` — "cannot tell" is
  not "no", and inverting that would nag every user without fontconfig.

Not yet built: configurators for anything other than starship, commitizen, pre-commit, docker,
bat, ansible and lazygit; `repo: local` pre-commit hook authoring and hooks outside the curated
catalog; lazygit keybindings and `customCommands` authoring (both preserved, never edited);
ansible fact-caching backends; and — for pre-commit and commitizen only — round-tripping an
existing hand-edited config back into wizard state. The four newer configurators *do* round-trip,
because JSON, INI, bat's flag list and lazygit's YAML are all flat enough to preserve faithfully;
pre-commit's is not, and that asymmetry is deliberate (see each spec's stack-decisions).

## Specs (`docs/specs/`)

Design documents live in `docs/specs/<feature>/` — `specifications.md` (numbered, testable
requirements), `stack-decisions.md` (choices *and* rejected alternatives with reasons), and
`development-plan.md` (milestones, testing strategy, risks). See `docs/specs/README.md` for the
conventions.

When working on a feature that has a spec, read it first and **keep it current in the same PR** —
a spec that no longer matches the code is worse than no spec. Record resolved open questions with
a date and the answer rather than deleting them, and when a live finding contradicts a
requirement, update the requirement and note what was learned. New features of any size should
get a spec directory before implementation starts.

## The agent (`devstuff agent`) — a third catalog subsystem

`src/dev_setup/agent/` + `agent_tools.yaml` is an interactive session where a local Ollama model
calls devstuff's tools plus a workspace-scoped filesystem/shell kit. It follows the same
catalog-driven shape as tools and functions: `agent/catalog.py` validates `agent_tools.yaml`
(bundled → user override), `agent/registry.py` turns it into `AgentTool` objects, and
`agent/primitives.py` dispatches by `impl` through a `_PRIMITIVES` dict — the same
strategy-dispatch pattern as `_INSTALLERS` in `generic.py`.

**Adding an agent tool**: if it bridges to something that already exists (a catalog tool, a
`functions.yaml` entry), it is a pure YAML edit — `impl: catalog` or `impl: function` plus a
`target`. Only a genuinely new mechanism needs a new `impl: primitive` callable registered in
`_PRIMITIVES`. `type: script` functions are auto-exposed as `fn_<key>` tools with no edit at all.

**Security invariants — do not weaken these without deliberate thought:**
- `Workspace.resolve()` in `agent/sandbox.py` is the *only* thing standing between the model and
  the filesystem. It resolves symlinks and `..` **before** the containment check; reordering that
  reintroduces a symlink escape. Prompt instructions are not a control and never will be.
- The command denylist (`check_command`) runs **before** any confirmation prompt and is
  deliberately not disabled by `--yolo`. The prompt is a human attention filter; attention
  degrades over a session, the denylist does not.
- Credential dirs are blocked for **read** as well as write — exfiltrating an SSH key into a model
  context is as bad as overwriting one. `~/.config/devstuff` is readable but not writable, so
  the agent cannot author catalogs (FR-14a).
- `assess()` (the launch guard) is advisory UX, not a control. Keep that distinction in comments;
  the risk is a future reader mistaking a warning for enforcement.

**Everything in the loop returns errors to the model rather than raising.** Unknown tool, bad
arguments, sandbox refusal, a crashing tool — all become `role: tool` messages so the agent can
re-plan. A malformed tool call must never end a session. `max_iterations` is what stops a runaway.

**`cd` is a tool, not a shell command**, for the same reason `shell-eval` functions exist: each
`run_command` is its own subprocess, so a shell `cd` evaporates on exit. And `shell-eval`
functions are excluded from the toolbox entirely — they exist to mutate the calling shell, which
an agent subprocess has no way to do (mirroring the guard in `run_cmd.py`).

**Ollama response-shape handling all lives in `ollama.parse_message()`.** Builds differ on
whether reasoning arrives in `message.thinking` or as inline `<think>` tags in `content`, and
whether tool calls arrive in `tool_calls` or as JSON inside `content`. Think-stripping must stay
*ahead* of the content-JSON fallback, or a reasoning preamble hides the tool call. Keep new
quirks in that one function.

**Model choice is measured, not assumed.** `ollama show` reports a `capabilities` array;
preflight requires `tools` in it. The default (`gemma4:latest`) was picked by running the same
scaffolding prompt across local models — lfm2.5 had `write_file` available and still shelled out
to `echo >`, corrupting the content through shell quoting. If you change the default, re-run that
comparison rather than reasoning from parameter counts.

`agent_tools.schema.json` is hand-maintained for editor tooling and **not** enforced at runtime —
same arrangement, and same drift hazard, as `functions.schema.json`.

First-run UX: `agent/wizard.py` builds `agent.yaml` on the first interactive run (host → a
pick-list of tool-capable models → reasoning visibility), re-runnable via `devstuff agent
--setup`. It configures only those three fields deliberately — a first-run wizard asking about
`num_ctx` and timeouts would be worse than one asking nothing; everything else keeps its default
and is hand-editable.

Not yet built: an `add` wizard for agent tools, and `catalog import`/`export` for them.

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
- **The agent's safety boundary is the workspace root, enforced in code.** Not a sandbox
  technology (bubblewrap/firejail) and not model instructions — `Path.resolve()` containment plus
  a command denylist, both unit-tested. Chosen so it works with zero new dependencies and fails
  closed; if you want stronger isolation, add it *around* this, not instead of it.
- **No new runtime dependencies for the agent.** The Ollama transport is stdlib `urllib` against
  `/api/chat`; the REPL uses `prompt_toolkit`, already vendored via questionary. devstuff is a
  globally installed CLI, so every dependency is a cost paid by users who never run `agent`.
- **Configuration is Python, installation is YAML.** The no-per-tool-code rule covers *install
  mechanisms*, which generalise; per-tool config formats don't. `configure/` is a registry of
  Python wizards on purpose — see the section above before trying to fold it into `tools.yaml`.
- **A config wizard previews with the real binary, not an approximation.** `starship prompt`
  against a temp config in a throwaway project is the source of truth; the offline renderer is a
  labelled fallback for when the tool isn't installed (and is what unit tests exercise).
- **Functions get a parallel catalog/registry instead of extending `GenericTool`.** The
  schemas diverge enough (no `requires` inference, a `params` list, no install/remove
  lifecycle) that folding them into the tool catalog would be lossy; some duplication with
  `catalog.py`/`registry.py` is an accepted tradeoff over a forced shared abstraction.
