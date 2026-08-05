# devstuff

A Python-based CLI for managing your Linux development environment. Install, remove, and track developer tools from a single command — with an interactive picker, a guided wizard for adding custom packages, and a consistent Rich terminal UI.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **OS** | Ubuntu 20.04+ or Debian 11+ (amd64) |
| **Python** | 3.11 or later |
| **curl** | Used by script-based installers (Docker, NVM, uv, etc.) |
| **sudo** | Required for tools that write to system paths (`/usr/local/bin`, apt packages) |
| **ca-certificates** | For HTTPS downloads — present on most systems by default |

These are available on any standard Ubuntu/Debian install. On a fresh minimal image, run:

```bash
sudo apt-get install -y python3 python3-pip curl ca-certificates sudo
```

**Optional** — only needed when using specific install types:

| Requirement | When |
|-------------|------|
| `git` | `git`-type custom packages (`devstuff add` → git) |
| `node` / `npm` | `npm`-type custom packages |
| `uv` | Running from source via `./devstuff` (auto-installed if missing) |

---

## Installation

### From PyPI (recommended)

The simplest install — no git clone required, Python 3.11+ is the only prerequisite:

```bash
# pipx gives the tool its own isolated environment (preferred)
pipx install devstuff

# or plain pip
pip install devstuff
```

After install, `devstuff` is the command. Run `devstuff --help` to verify.

### Diagnosing and repairing (`devstuff doctor`)

`devstuff doctor` runs a battery of health checks and reports the status of each. Checks
that can be auto-fixed are offered for repair interactively, or applied all at once with
`--fix`. Use `--check-only` for a read-only report (no fixes offered).

```bash
devstuff doctor              # run checks, offer to fix problems
devstuff doctor --fix        # run checks and auto-apply every available fix
devstuff doctor --check-only # report only, don't offer or apply fixes
```

The checks cover:

| Check | What it verifies | Auto-fixable |
|-------|-----------------|--------------|
| `python-version` | Python 3.11+ | — |
| `runtime-deps` | click, pyyaml, rich, questionary importable | — |
| `config-dir` | `~/.config/devstuff` exists and is writable | create it |
| `bundled-tools-catalog` | bundled `tools.yaml` loads and validates | — |
| `user-tools-catalog` | user `tools.yaml` (if present) is valid | — |
| `bundled-functions-catalog` | bundled `functions.yaml` loads and validates | — |
| `user-functions-catalog` | user `functions.yaml` (if present) is valid | — |
| `bundled-agent-catalog` | bundled `agent_tools.yaml` loads and validates | — |
| `user-agent-catalog` | user `agent_tools.yaml` (if present) is valid | — |
| `registry` | effective catalog builds into tool objects; `is_installed()` probes don't crash | — |
| `bashrc` | `~/.bashrc` is writable (needed by configurators and `functions enable`) | — |
| `stale-executable` | no stale `dev-setup` command on `$PATH` | remove symlink |
| `stale-packages` | no old `dev-setup` install reported by `uv`/`pipx` | uninstall |
| `old-dirs` | no old `~/.config/dev-setup` or `~/.local/share/dev-setup` | move to `devstuff` |
| `stale-bashrc-blocks` | no stale `# dev-setup:` / `# dev-setup-fn:` blocks in `~/.bashrc` | remove |

The last four checks detect leftover artifacts from pre-v1.19 installs (when the package
shipped a `dev-setup` command alias and used `~/.config/dev-setup` paths). `doctor --fix`
cleans them up: it uninstalls old `dev-setup` packages from `uv`/`pipx`, removes stale
symlinks (refusing to touch one that points into the current `devstuff` install), moves
old config/data directories to the new `devstuff` paths, and strips stale bashrc blocks —
verifying each fix before moving on.

#### Manual cleanup (pre-v1.19 artifacts)

If you prefer to clean up by hand, or `doctor` can't fix something automatically:

1. **Uninstall the old package(s).** The exact command depends on how you installed it:

   ```bash
   # uv tool (check first with: uv tool list)
   uv tool uninstall dev-setup devstuff

   # pipx
   pipx uninstall dev-setup
   pipx uninstall devstuff

   # plain pip
   pip uninstall dev-setup devstuff
   ```

   This removes the now-defunct `dev-setup` executable from `~/.local/bin` (a stale symlink
   there will keep resolving to the old version otherwise).

2. **Install the current version** (see [From PyPI](#from-pypi-recommended) above).

3. **Move your config and data to the new paths** (only if they exist on your machine):

   ```bash
   [ -d ~/.config/dev-setup ]      && mv ~/.config/dev-setup ~/.config/devstuff
   [ -d ~/.local/share/dev-setup ] && mv ~/.local/share/dev-setup ~/.local/share/devstuff
   ```

   Your `tools.yaml`, `functions.yaml`, `agent.yaml`, and `agent_tools.yaml` catalogs move
   with the directory and are picked up automatically on the next run.

4. **Clean up stale `~/.bashrc` blocks.** The bashrc patch markers were renamed, so
   `devstuff remove` / `devstuff functions disable` won't find blocks written by the old
   version. Search for and remove any leftover blocks:

   ```bash
   # Show any old blocks still in your bashrc:
   grep -nE '# dev-setup:|# dev-setup-fn:' ~/.bashrc
   ```

   Then either delete those blocks manually (each is a `# <marker>` line followed by its
   content up to the next blank line), or re-run the relevant configurator / `devstuff
   functions enable <key>` to write a fresh block under the new marker and remove the old
   one by hand. The `bat` configurator's block marker changed from `dev-setup: bat` to
   `devstuff: bat`; function blocks changed from `dev-setup-fn:<key>` to `devstuff-fn:<key>`.

### From source (development)

```bash
git clone <repo-url> ~/dev-setup-py
cd ~/dev-setup-py
bash install.sh   # installs from PyPI via pipx or pip
```

Or to run directly from the cloned repo without installing:

```bash
./devstuff list   # creates a .venv on first run, then stays fast
```

The `./devstuff` bash script requires Python 3.11+ and creates a local `.venv` automatically. On Debian/Ubuntu, if `python3-venv` is not installed, it falls back to `uv venv` if uv is available.

For editable development installs:

```bash
pip install -e .
devstuff list
```

---

## How it works

When installed from PyPI (via `pip` or `pipx`), `devstuff` is a standard Python entry point — Python is the only runtime dependency. The `[project.scripts]` entry in `pyproject.toml` maps `devstuff` directly to `dev_setup.__main__:main`.

The bash `./devstuff` script in the repo is a convenience runner for the git-clone workflow. It creates a `.venv` using `python3 -m venv` (falling back to `uv venv` on systems where `python3-venv` is a separate package) and installs the project in editable mode on first run.

---

## Verbose output (`-v` / `-vv`)

Every command accepts `-v`, before or after the subcommand. devstuff is a thin layer over other
people's commands; this is how you see them.

```bash
devstuff install lazygit -v      # log each command and stream its output live
devstuff -vv run whats-on-port 8080   # + probes, exit codes, and bash -x line tracing
DEVSTUFF_VERBOSE=1 devstuff update    # same as -v, for scripts and CI
```

| Level | What you get |
|-------|--------------|
| *(default)* | Normal output. Command output is captured and shown only if the command fails. |
| `-v` | Every command devstuff runs, printed as a line you can paste back into a shell, with its output streaming live. Spinners are replaced by plain lines so they don't fight with that output. |
| `-vv` | Plus read-only probes (version checks, install-state checks) with their exit codes and output, the body of any script before it runs, resolved function parameters, and `bash -x` tracing of every line inside function and installer scripts. |

`-vvv` and above are the same as `-vv`.

All of it goes to **stderr**, so it never contaminates anything you pipe or capture — including
`eval "$(devstuff run <shell-eval function>)"`, whose stdout has to be shell code and nothing else.

---

## Commands

### `list`

Show all available packages with their install status, type, version, and help command.

```bash
devstuff list                    # all packages
devstuff list core               # core category only
devstuff list tools              # tools category only
devstuff list custom             # custom/user-added packages only
devstuff list --installed        # only installed packages
devstuff list --available        # only packages not yet installed
```

Output columns: status (✔/✘), package key, description, install type, version (if installed), help command.

---

### `install`

Install one or more packages by key, or launch an interactive multi-select picker.

```bash
devstuff install docker nvm      # install specific packages
devstuff install                 # interactive picker (Space to toggle, Enter to confirm)
```

The interactive picker shows all available packages with their current install status and lets you select multiple at once before confirming.

---

### `remove`

Uninstall an installed package. Always asks for confirmation before proceeding.

```bash
devstuff remove htop
devstuff uninstall htop          # alias
```

---

### `update`

Update one or more already-installed packages to the latest version, or pin a single package to
a specific version with `--version`. With no arguments, launches an interactive multi-select
picker over installed packages, similar to `install`.

```bash
devstuff update nvm                    # update to latest
devstuff update pi --version 1.2.3     # pin a single package to a specific version
devstuff update                        # interactive picker
```

Packages that aren't installed are skipped with a warning rather than treated as an error.
`--version` can only be combined with a single package (and is not available in the interactive
picker).

The interactive picker probes every installed package for a newer version (`npm view`, `uv tool
list --outdated`, `apt-cache policy`, or comparing local vs. remote git HEAD) and pre-checks the
ones with a known update available. `script`/`bash` packages have no reliable way to check for
a newer version ahead of time, so they're listed as "unknown" and left unchecked — selecting one
still works, it just can't be pre-recommended.

How "update" is performed depends on the package's install `type`:

| Type | Latest | Specific version |
|------|--------|-------------------|
| `npm` | `npm install -g <pkg>@latest` | `npm install -g <pkg>@<version>` |
| `pip` / `uvx` | `uv tool upgrade <pkg>` | `uv tool upgrade <pkg>==<version>` |
| `apt` | `apt-get install --only-upgrade` | `apt-get install <pkg>=<version>` (single package only) |
| `git` | `git pull` (+ re-run `git_install_cmd`) | not supported — repos are cloned shallow (`--depth=1`) |
| `script` / `bash` | Re-runs the install script | not supported — no version parameter to inject |

For `script`/`bash` packages, "update" is a full reinstall (the same script that may have used
`sudo` runs again), since there's no narrower update mechanism available. `devstuff update` asks
for confirmation before doing this.

---

### `configure`

Set up an installed tool through a guided wizard, previewing the result before anything is
written. Installing a tool that has a wizard also offers to run it there and then.

```bash
devstuff configure                       # pick from the tools that have a wizard
devstuff configure starship              # run starship's prompt wizard
devstuff configure --list                # which tools have a wizard
devstuff configure starship --path       # print the config file path
devstuff configure starship --output /tmp/try.toml   # write elsewhere, leave the live config alone
```

| Tool | What it configures |
|------|-------------------|
| `ansible` | `ansible.cfg` — inventory paths, forks, pipelining, become and vault |
| `bat` | Theme, decorations, paging, and the man-page/`cat` shell integration |
| `commitizen` | Commit types, what each one bumps, git tags, and changelog sections |
| `docker` | `daemon.json` — log rotation, address pools and daemon behaviour |
| `lazygit` | Icons, diff pager, panels and git behaviour |
| `pre-commit` | Which git hooks run, when they run, and what they're allowed to rewrite |
| `starship` | Prompt style, colour palette, which sections appear, and layout |

Each wizard checks its result against the real tool before saving. What that check can prove
varies a lot, and the wizards say so rather than implying more than they know:

| Tool | What the tool itself checks | What the wizard has to check instead |
|------|-----------------------------|--------------------------------------|
| `ansible` | unknown keys, unknown sections, wrong types | **whether ansible actually *read* each setting** (`ansible-config dump`) — a key in a retired section validates and does nothing |
| `bat` | a bad `--style` component (hard error) | **theme names** — a bad theme is a stderr warning and exit 0, so a typo silently gives you the default forever |
| `docker` | key typos, malformed CIDRs, numeric log options | **five configs `dockerd --validate` accepts that stop every container from starting** |
| `lazygit` | wrong types (refuses to start) | **unknown keys and invalid enum values** — both silently ignored |
| `pre-commit` | the file's shape | **hook ids** — `validate-config` never opens a repository |

#### Commitizen wizard

Commitizen needs a config file before it does anything, and everything past its built-in
Conventional Commits rules means `cz_customize` — nine coupled regexes where `bump_map`'s keys
are matched against **group 1** of `bump_pattern`, the two breaking-change rules have to come
*first* because commitizen stops at the first key that matches, and `schema_pattern` has to
accept whatever `message_template` produces. Get the order wrong and `feat(api)!:` quietly ships
as a minor release.

The wizard asks about commit types and version rules, and derives all nine settings from the
answers:

| Step | Options |
|------|---------|
| **Convention** | `cz_conventional_commits` (commitizen's fixed rules — nothing to configure) · `cz_customize` (your types, your bump rules) |
| **Commit types** | Checkbox over `feat`, `fix`, `refactor`, `perf`, `docs`, `style`, `test`, `build`, `ci` (all on, matching Conventional Commits) plus `chore`, `revert`, `deps`, `security` — and you can add your own |
| **Per type** | What it bumps (`MAJOR` / `MINOR` / `PATCH` / no release), whether it appears in the changelog, under which heading, and its hotkey in `cz commit` |
| **Versioning** | Where the version lives (`commitizen`, `scm`, `pep621`, `uv`, `poetry`, `cargo`, `npm`, `composer`) · scheme (`semver`, `semver2`, `pep440`) · tag format · other files carrying the version |
| **Release** | Changelog on bump · `0.x` mode · annotated tags · GPG signing · incremental changelog · prerelease merging · `allow_abort` · hotkeys · changelog file · bump commit message |
| **Prompt** | Which questions `cz commit` asks — scope, body, footer |
| **Destination** | `pyproject.toml` (spliced in place) or a standalone `.cz.toml` |

The custom branch **starts as an exact copy of Conventional Commits** — the built-in type table
reproduces `commitizen.defaults.BUMP_MAP` and its picker, read out of the installed package and
pinned by a test. Switching to custom to add one type does not cost you the convention.

Before it asks anything, the wizard reads the project: git root, any existing commitizen config
(in commitizen's own search order, ignoring files without a `commitizen` section), the version
provider and current version from `pyproject.toml`/`package.json`/`Cargo.toml`/`composer.json`,
the latest git tag, and an existing changelog.

The review screen shows a sample `cz commit` message, the bump table, and the changelog sections:

```
  what each type does to 1.4.2

  commit                       increment  new version
  feat: …                      MINOR      1.5.0
  fix: …                       PATCH      1.4.3
  docs: …                      —          1.4.2
  feat!: …                     MAJOR      2.0.0
  BREAKING CHANGE: … (footer)  MAJOR      2.0.0
```

**That table is checked against the real thing.** "Check these rules against the real cz" builds
a throwaway git repo tagged at 1.4.2, replays one commit per bump level plus both breaking forms
through `cz bump --dry-run`, and reports whether commitizen agrees rule by rule. The same check
runs automatically before saving — a disagreement is reported and asks for confirmation, but
never blocks the save. "Preview a generated changelog" runs the real `cz changelog --dry-run`
over one sample commit per type. Without `cz` installed the wizard still works; the checks say so
rather than failing.

On save:

- Into `pyproject.toml`, every `[tool.commitizen…]` table is replaced and **the rest of the file
  is left byte for byte**. The result is parsed back and compared to what was meant; if it does
  not match, the write falls back to a standalone `.cz.toml` and the original is untouched.
- Any existing file is copied to `<name>.bak.<timestamp>` first, and a config that wasn't written
  by this wizard asks before being replaced.
- If more than one commitizen config now exists, you're told which one commitizen will actually
  read — `.cz.toml` beats `pyproject.toml`, so a new dotfile can silently shadow settings you
  forgot about.
- In a git repo with no `commit-msg` hook, you're offered one running `cz check`. An existing
  hook is never replaced.

The output is meant to be hand-edited afterwards: commented, ordered, and with regexes written as
TOML literal strings so `^((BREAKING[\-\ ]CHANGE|feat)(\(.+\))?!?):` reads as itself instead of
with every backslash doubled.

```toml
# Commitizen configuration
# Generated by `devstuff configure commitizen` — edit freely, or re-run the wizard.
# Convention: Custom types and bump rules · Types: feat, fix, docs, deps
# Option reference: https://commitizen-tools.github.io/commitizen/config/
[tool.commitizen]
name = 'cz_customize'
version_provider = 'pep621'
version_scheme = 'pep440'
tag_format = 'v$version'
update_changelog_on_bump = true
# …

# Prefix -> version increment. Order matters: commitizen stops at the first
# key that matches, so the two breaking-change rules have to lead.
[tool.commitizen.customize.bump_map]
'^.+!$' = 'MAJOR'
'^BREAKING[\-\ ]CHANGE' = 'MAJOR'
'^feat' = 'MINOR'
'^fix' = 'PATCH'
'^deps' = 'PATCH'
```

Design notes, including why this one isn't catalog-driven and why the checks warn rather than
veto: [`docs/specs/commitizen-config/`](docs/specs/commitizen-config/).

#### pre-commit wizard

pre-commit does nothing until **two** separate things are true: a `.pre-commit-config.yaml`
exists, *and* `pre-commit install` has written the git hooks. Neither implies the other, and a
repo with the first but not the second looks configured, validates cleanly, and never runs a
single check. Writing the file means knowing, per hook, which repo publishes it, its exact id,
what tag to pin, and which arguments are actually decisions — none of which is discoverable from
`--help`, so the usual workflow is copying someone else's config and hoping.

The wizard starts from a preset that already matches the project:

| Preset | What you get |
|--------|--------------|
| `Minimal` | Four hooks right for any repo, all of which fix rather than nag |
| `Essentials` | File hygiene, YAML/JSON/TOML parse checks, a private-key guard. No language tools |
| `Python (ruff)` | Essentials + `ruff-check` and `ruff-format` — the modern default |
| `Python, strict` | …plus `mypy`, `yamllint` and `gitleaks` |
| `Python (black + isort + flake8)` | The pre-ruff toolchain, for a project already on it |
| `JavaScript / TypeScript` | Essentials + `prettier` and `eslint` |
| `Shell scripts` | Essentials + `shellcheck`, `shfmt` and shebang checks |
| `Security first` | Essentials + `gitleaks`, AWS-key detection, no-commits-to-main |
| `Documentation` | Essentials + `markdownlint` and `yamllint` |
| `Match this project` | Essentials + the hooks for the languages actually found here |
| `Start from nothing` | Pick all ~38 hooks yourself |

Then a grouped checkbox over the full catalog (file hygiene · config files · Python · JavaScript ·
shell · Docker · docs · secrets · commit messages), each hook labelled with what it does and
whether it **rewrites files** or only reports.

Before it asks anything the wizard reads the project: git root, the languages present (via
`git ls-files`, so a vendored `node_modules` can't make a Python repo look like a JS one, and a
language needs two files to count), an existing config and the hook ids in it, **which git hook
types are currently installed**, and whether a commitizen config exists.

Several things are derived rather than asked:

- **`default_install_hook_types`** comes from the stages of the hooks you picked. `pre-commit
  install` installs *only* the `pre-commit` hook unless told otherwise — so adding commitizen's
  `commit-msg` hook without this gives you a config that validates, an install that reports
  success, and a hook that never fires. Nothing in pre-commit's output mentions it.
- **The pre-commit.ci `skip` list** is exactly the Docker-backed hooks. pre-commit.ci runs no
  Docker daemon, so those fail there with a container error rather than a lint result.
- **The suggested `exclude`** is built from the lockfiles actually in the repo, never a fixed list.
- **The commitizen hook** is only suggested when a commitizen config exists — it runs `cz check`
  and fails every commit without one.

The review screen lists every hook with its source repo, stage and effect, the prerequisites
anything needs (Docker, Node, a commitizen config), and warns about conflicting pairs — two
formatters over the same files don't error, they reformat each other's output on alternate
commits, forever.

**Checked against the real binary, at three levels split by what they cost:**

| Action | What it proves | Cost |
|--------|----------------|------|
| automatic, before every save | `pre-commit validate-config` passes, the emitted YAML parses back to what the model meant, and no selected stage is missing from `default_install_hook_types` | offline, milliseconds |
| *Clone the repos and prove every hook id exists* | Runs `pre-commit install-hooks`. **The only check that verifies hook ids** — `validate-config` reads the file's shape and never opens a repo, so a typo passes it and fails on your next commit | network, minutes |
| *Refresh every repository to its latest tag* | Runs the real `pre-commit autoupdate` and shows the diff | network, seconds |

A disagreement at save time is reported and asks for confirmation — it never blocks the save.
Without `pre-commit` installed the wizard still works; the checks say so rather than failing.

On save the config is written (any existing file copied to `<name>.bak.<timestamp>` first, and a
config not written by this wizard asks before being replaced, listing the ids it currently runs),
and then `pre-commit install` runs with the derived `--hook-type` flags. Opt out and you get the
exact command to run later.

```yaml
# pre-commit configuration
# Generated by `devstuff configure pre-commit` — edit freely, or re-run the wizard.
# Preset: Match this project · 14 hooks
#
# Install the git hooks:   pre-commit install --install-hook-types pre-commit,commit-msg
# Run against everything:  pre-commit run --all-files

# `pre-commit install` only installs the pre-commit hook unless it is told
# otherwise, so the hooks below that run at another stage need this to run at all.
default_install_hook_types: [pre-commit, commit-msg]

# Paths no hook ever looks at.
exclude: '^(.*/)?(uv\.lock)$'

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      # File hygiene
      - id: trailing-whitespace
      - id: check-added-large-files
        args: ['--maxkb=500']
      # Config files
      - id: check-yaml
      # Secrets and safety
      - id: detect-private-key
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.1
    hooks:
      - id: ruff-check
        args: ['--fix', '--exit-non-zero-on-fix']
      - id: ruff-format
```

Every repo, rev and hook id in the catalog was read from the real repository rather than recalled
— the revs are what `pre-commit autoupdate` resolves to, and the ids came from each repo's own
`.pre-commit-hooks.yaml` at that rev. Design notes, including why the verification is split by
cost and why quoting is decided by asking the YAML parser instead of a lookup table:
[`docs/specs/precommit-config/`](docs/specs/precommit-config/).

#### Starship prompt wizard

Four questions, with the prompt re-rendered after each one:

| Step | Options |
|------|---------|
| **Style** | `Plain text` · `Bracketed segments` (both ASCII, any font) · `Icons` · `Icons, bracketed` (Nerd Font glyphs) · `Powerline`, `Powerline, rounded`, `Powerline, slanted` (solid colour bars) |
| **Palette** | `Terminal colours` (inherits your terminal theme) · Catppuccin Mocha · Nord · Gruvbox Dark · Tokyo Night · Dracula · Rosé Pine · Catppuccin Latte (light) |
| **Sections** | Grouped checkbox over ~35 modules — see the table below |
| **Layout** | Single line · Two lines · Two lines with shell info right-aligned (needs zsh/fish/nushell — bash has no right prompt) |

| Group | Sections |
|-------|----------|
| **Context** | username, hostname (over SSH), container (Docker/toolbox/distrobox) |
| **Location** | current directory |
| **Git** | branch, commit hash (when detached), status (dirty/ahead/behind), operation in progress |
| **Languages** | Node.js, Deno, Bun, Python, Rust, Go, Java, PHP, Ruby, Elixir, .NET, package version |
| **Infrastructure** | Docker context, **Docker Compose project**, Kubernetes context, AWS profile, Google Cloud project, Azure subscription, Terraform workspace, Nix shell |
| **Shell** | command duration, exit code of the last command, background jobs, nested shell depth, clock |

The review menu adds two content toggles: the blank line between prompts, and whether language
sections show **version numbers** or just the runtime's symbol.

If you pick a style that needs a Nerd Font and there isn't one on the machine, the wizard says
so in the style list itself (`Needs a Nerd Font — none installed here.`) and offers to install
`nerd-font` right there — once per run, whether you say yes or no. Two cases it handles rather
than pretending otherwise: over SSH it points you at [nerdfonts.com](https://www.nerdfonts.com/)
instead, because the glyphs are drawn by the terminal on *your* machine, not the one you're
configuring; and after installing it reminds you to actually select the font in your terminal's
settings, which is a preference no shell can change for you. Without `fontconfig` there is no way
to enumerate fonts, so the wizard says nothing rather than guessing.

The Compose section is a starship [custom module](https://starship.rs/config/#custom-commands)
rather than a built-in one — it reports the project name `docker compose` in that directory would
actually use (`$COMPOSE_PROJECT_NAME`, else a top-level `name:` in the compose file, else the
lowercased directory name), and it only runs at all when there is a compose file next to you.

The preview is the real thing: the candidate config is written to a temp file and rendered by
your installed `starship` binary inside a throwaway sample project (a git repo with language
marker files), so what you see is the bytes your prompt will produce. Without starship installed
the wizard still works and falls back to a labelled approximation.

A review menu then lets you revisit any answer, dump the generated TOML, or save. On save:

- Written to `$STARSHIP_CONFIG`, or `~/.config/starship.toml`.
- Any existing file is copied to `starship.toml.bak.<timestamp>` first — and if it wasn't written
  by this wizard, you're asked before it's replaced.
- If `~/.bashrc` has no starship hook, you're offered one (using the same marker
  `devstuff install starship` uses, so `devstuff remove starship` still cleans up).

The output is meant to be hand-edited afterwards: it's commented, ordered, and colours are
referenced through nine semantic palette roles (`dir`, `git`, `lang`, `infra`, `shell`, `ok`,
`err`, `muted`, `bar_text`), so retheming everything is nine edits in one table.

```toml
# Starship prompt configuration
# Generated by `devstuff configure starship` — edit freely, or re-run the wizard.
# Style: Powerline · Palette: Nord · Layout: Two lines
# Module reference: https://starship.rs/config/
"$schema" = 'https://starship.rs/config-schema.json'

add_newline = true
palette = 'nord'

format = """
[](fg:dir)\
$directory\
[](fg:dir bg:git)\
$git_branch\
[](fg:git)\
$line_break\
$character"""

# Semantic colour roles — swap these to retheme every section at once.
[palettes.nord]
dir = '#81a1c1'
git = '#a3be8c'
# …
```

#### Docker daemon wizard

Docker's default log driver writes **uncapped** JSON files under `/var/lib/docker`. A chatty
container grows them until the partition is full, at which point the daemon and usually the
host stop working. Nothing warns about it, and it is the most common way a Docker host dies.

`daemon.json` has its own trap: `dockerd --validate` accepts five configurations that let the
daemon start healthy and then make **every `docker run` fail** with an error that never
mentions the config file. All measured against Docker 29.6:

| config | `dockerd --validate` | what actually happens |
|--------|----------------------|------------------------|
| a key typo, a malformed CIDR, a numeric `max-size` | **rejected** | — |
| `"log-driver": "nosuchdriver"` | accepted | every container fails to start |
| an unknown log option for the chosen driver | accepted | every container fails to start |
| `max-file: "1"` with `compress: "true"` | accepted | every container fails to start |
| an address pool `size` below its base prefix | accepted | no usable networks |
| `"hosts"` alongside a systemd unit passing `-H` | accepted | daemon refuses to start |

The wizard runs `--validate` *and* every check in that table it does not do.

| Preset | What it sets |
|--------|--------------|
| **Log rotation only** | A size cap and nothing else — the safe minimum |
| **Workstation** | Rotation, live-restore, faster parallel pulls |
| **Server** | Bigger log budget, compression, metrics on loopback, `no-new-privileges` |
| **CI runner** | Small logs, fast pulls, no live restore |
| **Hand logs to systemd** | journald owns container output and its own rotation |
| **Behind a corporate network** | Rotation plus address pools that avoid the usual VPN collisions |
| **Whatever is on this machine now** · **Start from nothing** | |

Three things are derived rather than asked: the `log-opts` are filtered to what the chosen
driver accepts (`journald` rejects `max-size`, and would otherwise break every container),
only non-default values are written (so a future Docker changing a default still reaches you),
and any key in an existing `daemon.json` the wizard doesn't model is carried through untouched.

Saving writes through `sudo install` — shown before it runs, staged via a temp file so a failed
`sudo` can't truncate the existing config. The **restart is a separate question**, because it
stops running containers unless `live-restore` was already on *before* it, and it reports how
many containers that is.

```json
{
  "log-driver": "local",
  "log-opts": { "max-size": "10m", "max-file": "3", "compress": "true" }
}
```

#### bat wizard

bat ships 28 themes and no way to see one without typing its name, so the wizard renders a
sample file **through the real `bat`** on every pass — choosing a theme is looking at it.

It also catches bat's one quiet failure: a theme name bat doesn't have produces a warning on
stderr (which a pager swallows) and **exit 0**, so a typo silently gives you the default theme
forever. Every theme named is checked against `bat --list-themes`, which is read at run time
so themes you built yourself are offered too.

| Preset | What it sets |
|--------|--------------|
| **Balanced** | bat's defaults, with a light/dark theme pair that follows your terminal |
| **Minimal** | No decorations, no pager — closest to plain `cat` |
| **Line numbers only** | Numbers and nothing else, so output stays copy-pasteable |
| **Code review** | Numbers, git changes, grid, both headers, italics |
| **Friendly to pipes** | No pager, no wrapping, no decorations |
| **Whatever is configured now** · **Start from nothing** | |

The shell integration is offered too, since the best thing bat does isn't in its config file
at all: syntax-highlighted man pages (`MANPAGER` + `MANROFFOPT`), a `bathelp` function, and the
`cat` alias — which carries the caveat that scripts still get the real `cat`, because aliases
are interactive-only.

```ini
# Generated by devstuff — devstuff configure bat
--theme-dark="Monokai Extended Origin"
--theme-light="GitHub"
--style="numbers,grid,header-filename,header-filesize,changes,snip"
--italic-text="always"
```

#### Ansible wizard

`ansible.cfg` is the config file most likely to be copied from a tutorial that's years out of
date, because ansible-core keeps moving settings and never tells you when one stops working.
Measured against ansible-core 2.20:

| what you write | `ansible-config validate` | does it work? |
|----------------|---------------------------|---------------|
| `[ssh_connection]` + `pipelining` | unknown section | **no** — absent from `dump` entirely |
| `stdout_callback = yaml` | fine | **no** — that callback was removed |
| `pipelining = "True"` (quoted) | fine | **inverted** — read as False |
| `inventory = "./inv"` (quoted) | fine | **no** — a path containing quotes |
| `callback_result_format = yaml` | **unknown key** | **yes** — the validator is wrong |
| any of the above, in a world-writable directory | fine | **no** — the file is ignored wholesale |

So validation isn't authoritative in *either* direction. The wizard's real check is
`ansible-config dump --only-changed`, which lists what ansible actually **read** from the file
— every setting written has to appear there.

| Preset | What it sets |
|--------|--------------|
| **Project defaults** | Readable output, sensible paths, and the speed settings that matter |
| **Fast** | Many forks, pipelining, cached facts — for large inventories |
| **CI runner** | No host-key prompts, terse output, no cows |
| **With vault** | Project defaults plus a vault password file |
| **Escalating by default** | Project defaults plus `become` |
| **Whatever is configured now** · **Start from nothing** | |

Values are written **unquoted**, deliberately: a quoted path becomes a path containing quote
characters, and a quoted boolean is read as `False`. A retired section in an existing file is
*preserved and reported*, never silently migrated — the wizard can't know whether an older
ansible elsewhere is still reading it.

#### lazygit wizard

lazygit refuses to start on a value of the wrong **type**, and silently ignores an unknown
**key** or an invalid enum value. So a config assembled from blog posts starts perfectly and
does a fraction of what it says — `git.paging.useConfig` appears in most delta guides and no
longer exists; `nerdFontsVersion: "9"` is accepted and draws no icons.

Every key the wizard writes was verified real by turning that asymmetry into a probe: set the
key to a value of obviously the wrong type and start lazygit — a real key errors, an unknown
one is ignored. (`lazygit --config` looks like a key list and isn't: it omits every setting
with no default, including `git.paging.pager`, the delta integration everyone wants.)

| Preset | What it sets |
|--------|--------------|
| **Recommended** | Icons, a readable graph, startup popups off |
| **No icons** | The same, minus the Nerd Font glyphs |
| **With delta** | Recommended plus delta as the diff pager |
| **Minimal interface** | No command log, bottom line or tips |
| **Careful** | Confirm on quit, no force pushing, no background fetching |
| **Whatever is configured now** · **Start from nothing** | |

Icons are gated on the Nerd Font check the starship wizard already uses — including its
"can't tell" answer, which means stay silent rather than nag. Your `customCommands` and
`keybinding` trees are preserved untouched; the wizard models neither.

---

### `add`

Guided wizard to register a new custom package. Supports six install types:

| Type | What it does |
|------|-------------|
| `npm` | `npm install -g <package>` |
| `uvx` | `uv tool install <package>` |
| `apt` | `sudo apt-get install -y <packages>` |
| `git` | `git clone --depth=1 <url>` with optional post-clone and pre-remove commands |
| `script` | `curl -fsSL <url> \| sh` — single-URL convenience script |
| `bash` | Arbitrary multi-step bash — opens `$EDITOR` for install and remove scripts |

```bash
devstuff add
```

The wizard collects type-specific fields, then prompts for a help command (e.g. `tool --help`). Packages are saved into `~/.config/devstuff/tools.yaml`.

#### `bash` type

For tools like AWS CLI or saml2aws that require multiple download/extract/install steps, choose the `bash` type. The wizard opens `$EDITOR` twice — once for the install script and once for the optional remove script — with a `#!/usr/bin/env bash / set -euo pipefail` template pre-filled.

Example YAML for a `bash`-type custom package:

```yaml
version: 1
tools:
  batcat:
    name: batcat
    description: Modern cat with syntax highlighting
    category: custom
    type: bash
    check_cmd: bat
    help_cmd: bat --help
    install_script: |
      set -euo pipefail
      VER=$(curl -s https://api.github.com/repos/sharkdp/bat/releases/latest | grep tag_name | cut -d'"' -f4 | sed 's/v//')
      curl -fsSL "https://github.com/sharkdp/bat/releases/download/v${VER}/bat_${VER}_amd64.deb" -o /tmp/bat.deb
      sudo dpkg -i /tmp/bat.deb
      rm /tmp/bat.deb
    remove_script: |
      sudo dpkg -r bat
```

---

### `delete`

Remove a user catalog entry from the registry. Built-in-only packages cannot be deleted, but a user override of a built-in package can be deleted to restore the bundled definition.

```bash
devstuff delete my-tool
devstuff rm my-tool              # alias
```

Asks for confirmation, then removes the entry from `~/.config/devstuff/tools.yaml`.

---

### `catalog`

Manage the user YAML catalog.

```bash
devstuff catalog path                 # print ~/.config/devstuff/tools.yaml
devstuff catalog export               # write ./devstuff-tools.yaml
devstuff catalog export tools.yaml    # write effective catalog to a path
devstuff catalog import tools.yaml    # validate and merge into user catalog
```

The effective catalog is loaded in this order:

1. Bundled tools from `src/dev_setup/tools.yaml`
2. Legacy JSON migration from `~/.config/devstuff/packages/*.json`
3. User overrides and additions from `~/.config/devstuff/tools.yaml`

When a user key matches a bundled key, the user definition overrides the bundled definition in place. New user keys are appended after bundled tools.

---

### `skills`

Interactively clone a GitHub repository and copy its skills into `claude`, `codex`, and/or `pi`.

```bash
devstuff skills add
```

You'll be prompted for:

- **Repository** — `owner/repo` or a full URL.
- **Targets** — which of `claude` (`~/.claude/skills`), `codex` (`~/.codex/skills`), and `pi`
  (`~/.pi/skills`) to install into.

The repo is cloned anonymously first. If that fails (private repo), you're prompted to
authenticate via an SSH key file or a GitHub personal access token. If the repo has a top-level
`skills/` directory, every subdirectory under it is treated as its own skill; otherwise the whole
repo is treated as a single skill. Existing skill directories are only overwritten after
confirmation.

---

## Agent

`devstuff agent` opens an interactive session with a **local** model (via [Ollama](https://ollama.com))
that can call devstuff's own tools plus a workspace-scoped filesystem/shell kit. Nothing leaves
the machine, there are no API keys, and it works offline.

```bash
devstuff agent                                  # interactive REPL
devstuff agent --setup                          # (re)run the configuration wizard
devstuff agent --dir ~/projects                 # skip the workspace prompt
devstuff agent --model granite4.1:8b            # override the configured model
devstuff agent --print "which node tools do I have?"   # one-shot, non-interactive
```

```
you ❯ create a python project called xyz-project with a hello world main.py

  ↳ run_command(command='mkdir xyz-project')

  ╭─ run in ./xyz-project ──────────────────────╮
  │ mkdir xyz-project                           │
  ╰─────────────────────────────────────────────╯
  ? Run run_command?  Yes / No / Always allow this tool
```

### Setup

First, make sure a tool-capable model is available:

```bash
devstuff install ollama
ollama pull gemma4          # or any model reporting the `tools` capability
```

Then the **first time you run `devstuff agent`** with no configuration, a wizard walks you
through it — Ollama host, then a pick-list of your locally available tool-capable models, then
whether to show the model's reasoning. It writes `~/.config/devstuff/agent.yaml` and continues
into the session. Re-run it any time with `devstuff agent --setup`.

The wizard only lists models that report the `tools` capability (`ollama show <model>` shows it
under Capabilities), so you can't accidentally pick one that can't call tools. `devstuff agent`
re-verifies this at startup regardless, and tells you which local models qualify if the
configured one doesn't.

The wizard is skipped when you pass `--model`/`--host` (you're already steering) or run
non-interactively (`--print`), which fall back to built-in defaults.

### Safety model

The workspace root, chosen at launch (prompted, defaulting to the current directory), is the
boundary:

- **Path containment** — every path a tool touches is resolved (collapsing `..` and following
  symlinks) and must land inside the workspace root. A symlink planted inside the workspace is
  not a way out.
- **Protected paths** — `~/.ssh`, `~/.aws`, `~/.gnupg` and `~/.config/gh` are refused for read
  *and* write even if the workspace root contains them. `~/.config/devstuff` is readable but
  never writable: catalog authoring stays a human action.
- **Confirmation** — every mutating tool call shows the exact command, or a unified diff for
  `write_file`, and waits for yes / no / always-this-session. Read-only calls run silently.
- **Denylist** — `sudo`, pipe-to-shell installers, catastrophic deletes, disk and service
  commands, and redirects out of the workspace are refused before any prompt is shown, and are
  **not** enabled by `--yolo`.
- **Launch guard** — warns before handing the agent `$HOME`, a system directory, or a git repo
  with uncommitted changes.

`--yolo` skips confirmations for a session; the denylist and path containment still apply.
In `--print` mode without `--yolo`, mutating calls are refused rather than auto-approved, so a
scripted invocation cannot become an unattended agent with write access.

### Configuration

`~/.config/devstuff/agent.yaml` (all fields optional):

```yaml
version: 1
model: gemma4:latest
host: http://localhost:11434     # a remote daemon works; the local binary is then not required
temperature: 0.2
num_ctx: 16384
think: false                     # true renders the model's reasoning, dimmed
max_iterations: 12               # tool calls per turn before the loop gives up
request_timeout: 120
command_timeout: 120
max_tool_output_bytes: 8000      # tool output is truncated to this before going back to the model
auto_approve: []                 # tool keys that never ask, e.g. ["write_file"]
deny_patterns: []                # extra regexes refused on top of the built-in denylist
```

### Tools

| Tool | Mutating | Purpose |
|------|----------|---------|
| `read_file` | | Read a UTF-8 file in the workspace |
| `write_file` | ! | Create or overwrite a file (shows a diff) |
| `list_dir` | | List a directory |
| `cd` | | Move the working directory used by later calls |
| `run_command` | ! | Run a shell command in the workspace |
| `list_tools` | | List the devstuff catalog with install state |
| `search_catalog` | | Find a catalog tool by name or description |
| `tool_info` | | Details for one catalog tool |
| `install_tool` | ! | Install a catalog tool (handles sudo itself) |
| `fn_<key>` | ! | Every `type: script` entry in `functions.yaml`, exposed automatically |

`cd` is a tool rather than a shell command because each `run_command` is its own subprocess — a
shell `cd` would evaporate when it exits. `shell-eval` functions are deliberately excluded: they
exist to mutate the calling shell, which a subprocess cannot do.

### agent_tools.yaml

The toolbox is a catalog, like everything else. Bundled at `src/dev_setup/agent_tools.yaml`,
user overrides at `~/.config/devstuff/agent_tools.yaml`, same merge precedence as `tools.yaml`.

```yaml
version: 1
expose_functions: true
tools:
  count_lines:
    name: Count Lines
    description: Count the lines in a file. Use this instead of reading a large file.
    impl: primitive          # primitive | catalog | function
    mutating: false
    params:
      - name: path
        type: string
        description: Path to the file, relative to the current directory.
        required: true
```

`impl: primitive` dispatches to a callable in `agent/primitives.py` keyed by the tool key (adding
a new one is a code change). `impl: catalog` and `impl: function` bridge to the tool registry and
`functions.yaml` respectively via `target`, and need no code. `src/dev_setup/agent_tools.schema.json`
documents every field for editor autocomplete; it is not enforced at runtime.

### Session state

- Prompt history: `~/.local/share/devstuff/agent/history`
- Transcripts: `~/.local/share/devstuff/agent/transcripts/<timestamp>.json`, written after every
  turn so a session that ends in a crash is still readable. `/history` shows the current session
  and the transcript path.

### Using the prompt

- **Enter** sends. **Alt+Enter** (or **Ctrl-J**) inserts a newline, for pasting or writing
  multi-line instructions.
- **Type `/`** for a completion menu of session commands *and* every available tool, mutating
  ones flagged with `!`. **Tab** cycles, **Enter** takes the highlighted entry.
- `/<tool>` describes a tool and its parameters, e.g. `/write_file`. It does not run it — the
  agent decides when tools run, so there is no back door around the confirmation flow.

In-session commands: `/tools`, `/history`, `/cwd`, `/model`, `/reset`, `/help`, `/exit`.

---

## Functions/Scripts

Reusable shell functions/snippets, tracked in a separate catalog from installable tools
(`~/.config/devstuff/functions.yaml`, same bundled+user precedence merge as `tools.yaml`).
Unlike tools, functions aren't installed/removed — they're invoked.

There are two function `type`s, because a `devstuff` command runs as its own child process
and can't mutate the shell that invoked it:

| Type | What it does | How you invoke it |
|------|---------------|--------------------|
| `script` | Runs as a subprocess (like a tool's `install_script`) — for anything that just calls other binaries/apps and doesn't need to change your shell's state. | `devstuff run <key> [args...]` — prompts for any missing required param. |
| `shell-eval` | For things that must mutate the *calling* shell — env vars, `cd`, aliases, agents. Has two `register` modes (see below). | Depends on `register`. |

`shell-eval` functions declare `register`:

- **`register: bashrc`** (default) — `devstuff functions enable <key>` patches a real shell
  function into `~/.bashrc` (idempotent, using the same patch/remove mechanism as tool
  bashrc blocks). After enabling, open a new shell (or `source ~/.bashrc`) and call the
  function directly by name — `devstuff` itself never runs it, since a child process
  can't export environment changes back to your interactive shell.
  ```bash
  devstuff functions enable ssh-agent-key
  source ~/.bashrc
  ssh-agent-key ~/.ssh/id_ed25519
  ```
  `devstuff functions disable <key>` removes it from `~/.bashrc`.
- **`register: eval`** — `devstuff run <key> [args]` resolves params and prints shell code
  to stdout only (no prompts, no formatting — anything else on stdout would corrupt the
  `eval` capture); missing required params are reported on stderr and exit non-zero instead.
  ```bash
  eval "$(devstuff run some-eval-function arg1)"
  ```

Other commands:

```bash
devstuff functions list      # show all functions, their type, and declared params
devstuff functions path      # print ~/.config/devstuff/functions.yaml
```

### functions.yaml schema

A JSON Schema documenting every field (`src/dev_setup/functions.schema.json`) mirrors the
validation in `functions_catalog.py` — point your editor's YAML language server at it for
inline docs/autocomplete/validation while hand-editing a functions catalog (in VS Code with
the YAML extension, add a `yaml.schemas` mapping to the file's path, or add a
`# yaml-language-server: $schema=<path>` comment at the top of the file, as the bundled
catalog does).

```yaml
version: 1
functions:
  ssh-agent-key:
    name: SSH Agent + Add Key
    description: Start ssh-agent in the current shell and add a key to it
    type: shell-eval
    register: bashrc
    params:
      - name: key_path
        description: Path to the SSH private key
        required: true
    script: |
      eval "$(ssh-agent -s)"
      ssh-add "$key_path"
    docs_url: https://www.ssh.com/academy/ssh/agent
```

Each `params` entry becomes a named shell variable in the script body (`$key_path`, not
positional `$1`) — the runner injects a prelude mapping real argv positions to those names
for `script`/bashrc-registered functions, or bakes the already-resolved, shell-quoted values
directly for `register: eval` (which has no argv channel of its own once `eval`'d).

#### Function fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | no | Display name shown in `functions list`. Defaults to the catalog key. |
| `description` | no | Short description shown in `functions list`. Defaults to `""`. |
| `category` | no | Group shown in `functions list` (grouped/sorted like tools). Freeform string, defaults to `custom`. |
| `type` | yes | `script` or `shell-eval` — see the type table above. |
| `register` | shell-eval only | `bashrc` (default) or `eval`. Rejected for `type: script`. |
| `params` | no | List of param objects (see below), resolved positionally in the order declared. |
| `script` | yes | The bash script body. References params by name (`"$key_path"`), not by position (`$1`). |
| `help_cmd` | no | Help command shown alongside the function in `functions list`. |
| `docs_url` | no | Documentation URL for this function. |

#### Param fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Shell variable name the param is bound to. Must be a valid shell identifier (letters/digits/underscore, not starting with a digit) and unique within the function. |
| `description` | no | Defaults to `""`. Shown as the prompt label when this param is missing and interactively promptable (`type: script` only — `shell-eval` never prompts). |
| `required` | no | Defaults to `true`. Whether the param must resolve to a non-empty value. An explicitly empty value (`devstuff run key ""`) counts as missing, same as not passing it at all. |
| `default` | no | Defaults to `""`. Fallback value used when nothing else resolves it. A required param *with* a default is always satisfied by it, so it never triggers a resolution error or (for `register: bashrc`) the runtime bash guard described below. |

Unknown fields fail validation, same as tools. A required param without a default behaves
differently per invocation path:
- `type: script` — prompts for it interactively (unless stdin isn't a terminal, in which case
  it's reported and the command exits non-zero rather than hitting an unreadable prompt).
- `register: eval` — reported on stderr and exits non-zero; never prompts, to keep stdout
  clean for `eval` capture.
- `register: bashrc` — `devstuff` is never involved when the enabled function is called
  directly, so enforcement happens inside the generated function itself: it fails loudly
  (message to stderr, `return 1`) if the argument is left blank at call time.

Not yet built: an `add` wizard and `catalog import`/`export` for functions, analogous to the
ones tools already have — for now, custom functions are hand-edited YAML at
`~/.config/devstuff/functions.yaml`.

### Built-in functions

| Key | Category | Type | Description | Args |
|-----|----------|------|--------------|------|
| `ssh-agent-key` | auth | shell-eval (bashrc) | Start ssh-agent in the current shell and add a key to it | `key_path` |
| `validate-docker-compose` | validation | script | Validate a docker-compose.yml file in the current directory | — |
| `validate-yaml` | validation | script | Validate a YAML file's syntax using `yq` | `file` |
| `whats-on-port` | network | script | Find which process is listening on a port | `port`, `protocol` (optional: `tcp`/`udp`/`all`) |
| `acc-check` | web-dev | script | Run the pi coding agent's `/dogfood` skill against a web URL | `url`, `instruction` (optional) |
| `aws-saml-reauth` | web-dev | script | Reauthorize the AWS CLI via `saml2aws login --force` | `profile` (optional) |

#### `whats-on-port`

```bash
devstuff run whats-on-port 8080          # tcp and udp
devstuff run whats-on-port 5432 tcp      # one protocol
```

Prints the matching sockets, then the PID, user, elapsed time and **untruncated** command
line of every process holding them — plural because `SO_REUSEPORT` lets several processes
share one port, and "which of these five node processes" is usually the actual question.

It uses `ss` rather than `lsof` or `fuser` for one measured reason: **run unprivileged
against another user's socket, `lsof` and `fuser` print nothing and exit as though the port
were free.** `ss` still lists the socket, just without the `users:((...))` field — so the
function can tell "nothing there" apart from "something there I'm not allowed to name", and
says which. When the process is hidden it retries under `sudo -n`, and only if that needs no
password; a diagnostic shouldn't stop to prompt for one. If sudo isn't available it says
what to re-run.

"Nothing is listening" is reported as a success, not a failure, along with the two reasons a
bind can still fail afterwards: a container port published with `userland-proxy: false` is
NAT-forwarded with no host socket to find at all, and sockets in another network namespace
are invisible. If Docker is running, published container ports are matched and named too —
`docker-proxy`'s own command line doesn't say which container it belongs to.

`ss` comes from `iproute2`, which isn't a devstuff package and isn't on every minimal image,
so a missing `ss` names the apt package instead of surfacing "command not found".

---

## Built-in packages

### Core

These are the foundation tools — install them on every machine.

| Key | Name | Description | Help |
|-----|------|-------------|------|
| `docker` | Docker | Container runtime + docker compose plugin (`devstuff configure docker`) | `docker --help` |
| `nvm` | NVM + Node LTS | Node Version Manager + latest Node LTS | `nvm help` |
| `uv` | uv | Astral Python package and project manager | `uv --help` |

### Tools

Optional utilities you may want on some machines.

| Key | Name | Description | Help |
|-----|------|-------------|------|
| `ansible` | Ansible | Automation engine for configuration management and app deployment (`devstuff configure ansible`) | `ansible --help` |
| `aws` | AWS CLI | Amazon Web Services CLI v2 | `aws help` |
| `bat` | bat | cat replacement with syntax highlighting and git integration (`devstuff configure bat`) | `bat --help` |
| `commitizen` | Commitizen | Conventional-commit prompt, semantic version bumping, and changelog generation (`devstuff configure commitizen`) | `cz --help` |
| `eza` | eza | Modern ls replacement with git status, icons, and tree view | `eza --help` |
| `gh` | GitHub CLI | GitHub's official CLI | `gh --help` |
| `htop` | htop | Interactive process and resource monitor | `man htop` |
| `lazygit` | lazygit | TUI git client for fast, keyboard-driven git workflows (`devstuff configure lazygit`) | `lazygit --help` |
| `mkcert` | mkcert | Zero-config local HTTPS certificates | `mkcert --help` |
| `nerd-font` | JetBrainsMono Nerd Font | Patched font supplying the icons Starship and other CLI tools draw | `fc-list \| grep -i "nerd font"` |
| `ollama` | Ollama | Run large language models locally | `ollama --help` |
| `php` | PHP 8.4 | PHP 8.4 + common extensions via ondrej/php PPA | `php --help` |
| `pi` | Pi Coding Agent | AI coding agent npm package | `pi --help` |
| `pre-commit` | pre-commit | Git hook manager for automated code quality checks (`devstuff configure pre-commit`) | `pre-commit --help` |
| `saml2aws` | saml2aws | SAML → AWS STS credentials CLI (Versent) | `saml2aws --help` |
| `starship` | Starship | Fast, cross-shell customizable prompt (`devstuff configure starship`) | `starship --help` |
| `yq` | yq | Portable command-line YAML/JSON/XML processor | `yq --help` |

### Languages

| Key | Name | Description | Help |
|-----|------|-------------|------|
| `go` | Go | Go programming language toolchain | `go help` |
| `java` | Java 21 (OpenJDK) | OpenJDK 21 LTS - JDK and JRE | `java --help` |
| `ruby` | Ruby (rbenv) | Ruby via rbenv version manager + ruby-build | `ruby --version` |

---

## Custom packages

Custom packages live in `~/.config/devstuff/tools.yaml`. You can create them via `devstuff add`, import them with `devstuff catalog import`, or edit the YAML by hand.

### YAML schema

```yaml
version: 1
tools:
  my-tool:
    name: My Tool
    description: Does something useful
    category: custom
    type: bash
    check_cmd: my-tool
    help_cmd: my-tool --help
    docs_url: https://example.com/docs
    requires: []
    install_script: |
      set -euo pipefail
      curl -fsSL https://example.com/install.sh | sh
    remove_script: |
      rm -f "$HOME/.local/bin/my-tool"
```

### YAML fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Display name shown in `list` |
| `description` | no | Short description shown in `list` |
| `category` | no | `custom` (default), `core`, `tools`, or `languages` |
| `type` | yes | `npm`, `pip`, `uvx`, `apt`, `git`, `script`, or `bash` |
| `check_cmd` | no | Binary name or shell check used to detect install status |
| `help_cmd` | no | Command shown in `list` under the package entry |
| `docs_url` | no | URL opened by `devstuff docs <key>` |
| `requires` | no | List of package keys that must already be installed |
| `npm_name` | npm | npm package name |
| `pip_name` | pip | PyPI package name |
| `apt_packages` | apt | Space-separated list of apt packages |
| `git_url` | git | Repository URL to clone |
| `git_install_cmd` | git | Bash command run inside the cloned repo after clone |
| `git_remove_cmd` | git | Bash command run inside the repo before deletion |
| `script_url` | script | URL passed to `curl -fsSL … \| sh` |
| `install_script` | bash | Full bash script to run on install |
| `remove_script` | bash | Full bash script to run on remove |

Unknown fields fail validation. `requires` defaults to `["nvm"]` for `npm` tools and `["uv"]` for `pip`/`uvx` tools unless explicitly set.

### Examples

**npm package:**
```yaml
version: 1
tools:
  prettier:
    name: Prettier
    description: Opinionated code formatter
    type: npm
    npm_name: prettier
    check_cmd: prettier
    help_cmd: prettier --help
```

**uvx/PyPI package:**
```yaml
version: 1
tools:
  httpie:
    name: httpie
    description: Human-friendly HTTP client
    type: uvx
    pip_name: httpie
    check_cmd: http
    help_cmd: http --help
```

**apt package:**
```yaml
version: 1
tools:
  ripgrep:
    name: ripgrep
    description: Fast recursive search tool
    type: apt
    apt_packages: ripgrep
    check_cmd: rg
    help_cmd: rg --help
```

**Multi-step bash install:**
```yaml
version: 1
tools:
  saml2aws-custom:
    name: saml2aws (custom)
    description: SAML-to-AWS credential helper
    type: bash
    check_cmd: saml2aws
    help_cmd: saml2aws --help
    install_script: |
      set -euo pipefail
      VER=$(curl -s https://api.github.com/repos/Versent/saml2aws/releases/latest | grep tag_name | cut -d'v' -f2 | cut -d'"' -f1)
      curl -fsSL "https://github.com/Versent/saml2aws/releases/download/v${VER}/saml2aws_${VER}_linux_amd64.tar.gz" | tar -xz -C /tmp
      sudo mv /tmp/saml2aws /usr/local/bin/saml2aws
      sudo chmod +x /usr/local/bin/saml2aws
    remove_script: |
      sudo rm -f /usr/local/bin/saml2aws
```

---

## Architecture

```
dev-setup-py/
├── devstuff              # Bash entry point — bootstraps uv, then exec's Python
├── install.sh             # Installs devstuff and exposes the devstuff command
├── pyproject.toml         # Python project (hatchling, requires-python >=3.11)
└── src/
    └── dev_setup/
        ├── __main__.py    # python -m dev_setup entry point
        ├── cli.py         # Click group, command registration
        ├── base.py        # Tool ABC, patch_bashrc / remove_bashrc_block utilities
        ├── catalog.py     # YAML catalog loading, validation, migration, import/export
        ├── registry.py    # Loads bundled + user YAML into the live tool registry
        ├── generic.py     # GenericTool - handles all catalog install types
        ├── tools.yaml     # Bundled built-in tool catalog
        ├── functions_catalog.py   # YAML catalog loading/validation for functions.yaml
        ├── functions_registry.py # Loads bundled + user YAML into the live function registry
        ├── function_runner.py    # Param resolution + script/eval/bashrc rendering & execution
        ├── functions.yaml        # Bundled built-in function catalog
        ├── configure/              # Per-tool setup wizards (see "configure" above)
        │   ├── __init__.py         # CONFIGURATORS registry: tool key -> wizard module
        │   └── starship/
        │       ├── model.py        # presets, palettes, sections, StarshipConfig
        │       ├── render.py       # starship.toml emitter + offline preview renderer
        │       ├── preview.py      # live preview via `starship prompt` in a sample project
        │       └── wizard.py       # the interactive flow, backup + save
        ├── agent_tools.yaml       # Bundled agent tool catalog
        ├── agent/                 # Local-model agent (see "Agent" above)
        │   ├── config.py          # agent.yaml load/validate/save
        │   ├── wizard.py          # first-run setup wizard (devstuff agent --setup)
        │   ├── ollama.py          # stdlib-urllib client for /api/chat, /api/tags, /api/show
        │   ├── preflight.py       # installed / reachable / pulled / tool-capable checks
        │   ├── sandbox.py         # Workspace containment + command denylist + launch guard
        │   ├── catalog.py         # agent_tools.yaml load/validate/merge
        │   ├── registry.py        # AgentTool -> Ollama tool schema; function auto-exposure
        │   ├── primitives.py      # _PRIMITIVES dispatch: read_file/write_file/list_dir/cd/run_command
        │   ├── bridges.py         # catalog + functions.yaml bridges
        │   ├── approval.py        # confirmation prompts, unified diffs
        │   ├── loop.py            # tool-calling loop
        │   ├── transcript.py      # per-session JSON transcript
        │   └── session.py         # REPL, slash commands
        ├── ui.py          # Rich console helpers, questionary wrappers, styled prompts
        ├── commands/
        │   ├── list_cmd.py
        │   ├── install_cmd.py
        │   ├── remove_cmd.py
        │   ├── update_cmd.py
        │   ├── add_cmd.py
        │   ├── delete_cmd.py
        │   ├── catalog_cmd.py
        │   ├── run_cmd.py
        │   ├── functions_cmd.py
        │   ├── skills_cmd.py
        │   └── agent_cmd.py
└── docs/
    └── specs/             # Design docs per feature (see docs/specs/README.md)
        └── agent/
```

### Adding a new built-in tool

Add an entry to `src/dev_setup/tools.yaml`. Built-ins use the same schema as user tools, with `category` set to `core`, `tools`, or `languages`.

```yaml
mytool:
  name: My Tool
  description: Does something useful
  category: tools
  type: bash
  check_cmd: mytool
  help_cmd: mytool --help
  docs_url: https://example.com/docs
  install_script: |
    set -euo pipefail
    curl -fsSL https://example.com/install.sh | sh
  remove_script: |
    sudo rm -f /usr/local/bin/mytool
```

### Key design decisions

- **uv owns Python provisioning.** The bash wrapper only guarantees uv is present; Python version and virtualenv management is delegated entirely to `uv run`.
- **Catalogs are the source of truth.** Bundled YAML loads first, then user YAML overrides matching keys and appends new tools.
- **Tool execution is generic.** The Python engine handles npm, uvx/pip, apt, git, script URLs, and bash scripts from catalog metadata.
- **Custom packages are plain YAML.** Scripts are stored as strings and written to a temp file at install time, giving bash full parsing fidelity.
- **`install()` raises on failure.** Tools raise `RuntimeError` or `subprocess.CalledProcessError`; command handlers catch and report them. No `InstallResult` enum to check.
- **Invalid catalogs fail visibly.** Malformed YAML, unsupported versions, bad keys, unknown fields, and invalid `requires` values raise clear load errors.

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `click` | ≥ 8.1 | CLI command dispatch, `--help` generation, editor integration |
| `PyYAML` | ≥ 6.0 | Tool catalog parsing and writing |
| `rich` | ≥ 13.0 | Terminal UI — panels, tables, spinners, styled text |
| `questionary` | ≥ 2.0 | Interactive prompts — multi-select, confirm, text input |
