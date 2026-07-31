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
| `uv` | Running from source via `./dev-setup` (auto-installed if missing) |

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

After install, `devstuff` is the primary command. Run `devstuff --help` to verify. The former
`dev-setup` command remains available as a compatibility alias.

### From source (development)

```bash
git clone <repo-url> ~/dev-setup-py
cd ~/dev-setup-py
bash install.sh   # installs from PyPI via pipx or pip
```

Or to run directly from the cloned repo without installing:

```bash
./dev-setup list   # creates a .venv on first run, then stays fast
```

The `./dev-setup` bash script requires Python 3.11+ and creates a local `.venv` automatically. On Debian/Ubuntu, if `python3-venv` is not installed, it falls back to `uv venv` if uv is available.

For editable development installs:

```bash
pip install -e .
devstuff list
```

---

## How it works

When installed from PyPI (via `pip` or `pipx`), `devstuff` is a standard Python entry point — Python is the only runtime dependency. The `[project.scripts]` entries in `pyproject.toml` map both `devstuff` and the compatibility alias `dev-setup` directly to `dev_setup.__main__:main`.

The bash `./dev-setup` script in the repo is a convenience runner for the git-clone workflow. It creates a `.venv` using `python3 -m venv` (falling back to `uv venv` on systems where `python3-venv` is a separate package) and installs the project in editable mode on first run.

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
| `commitizen` | Commit types, what each one bumps, git tags, and changelog sections |
| `starship` | Prompt style, colour palette, which sections appear, and layout |

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

The wizard collects type-specific fields, then prompts for a help command (e.g. `tool --help`). Packages are saved into `~/.config/dev-setup/tools.yaml`.

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

Asks for confirmation, then removes the entry from `~/.config/dev-setup/tools.yaml`.

---

### `catalog`

Manage the user YAML catalog.

```bash
devstuff catalog path                 # print ~/.config/dev-setup/tools.yaml
devstuff catalog export               # write ./dev-setup-tools.yaml
devstuff catalog export tools.yaml    # write effective catalog to a path
devstuff catalog import tools.yaml    # validate and merge into user catalog
```

The effective catalog is loaded in this order:

1. Bundled tools from `src/dev_setup/tools.yaml`
2. Legacy JSON migration from `~/.config/dev-setup/packages/*.json`
3. User overrides and additions from `~/.config/dev-setup/tools.yaml`

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
whether to show the model's reasoning. It writes `~/.config/dev-setup/agent.yaml` and continues
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
  *and* write even if the workspace root contains them. `~/.config/dev-setup` is readable but
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

`~/.config/dev-setup/agent.yaml` (all fields optional):

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
user overrides at `~/.config/dev-setup/agent_tools.yaml`, same merge precedence as `tools.yaml`.

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

- Prompt history: `~/.local/share/dev-setup/agent/history`
- Transcripts: `~/.local/share/dev-setup/agent/transcripts/<timestamp>.json`, written after every
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
(`~/.config/dev-setup/functions.yaml`, same bundled+user precedence merge as `tools.yaml`).
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
devstuff functions path      # print ~/.config/dev-setup/functions.yaml
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
`~/.config/dev-setup/functions.yaml`.

### Built-in functions

| Key | Category | Type | Description | Args |
|-----|----------|------|--------------|------|
| `ssh-agent-key` | auth | shell-eval (bashrc) | Start ssh-agent in the current shell and add a key to it | `key_path` |
| `validate-docker-compose` | validation | script | Validate a docker-compose.yml file in the current directory | — |
| `validate-yaml` | validation | script | Validate a YAML file's syntax using `yq` | `file` |
| `acc-check` | web-dev | script | Run the pi coding agent's `/dogfood` skill against a web URL | `url`, `instruction` (optional) |
| `aws-saml-reauth` | web-dev | script | Reauthorize the AWS CLI via `saml2aws login --force` | `profile` (optional) |

---

## Built-in packages

### Core

These are the foundation tools — install them on every machine.

| Key | Name | Description | Help |
|-----|------|-------------|------|
| `docker` | Docker | Container runtime + docker compose plugin | `docker --help` |
| `nvm` | NVM + Node LTS | Node Version Manager + latest Node LTS | `nvm help` |
| `uv` | uv | Astral Python package and project manager | `uv --help` |

### Tools

Optional utilities you may want on some machines.

| Key | Name | Description | Help |
|-----|------|-------------|------|
| `aws` | AWS CLI | Amazon Web Services CLI v2 | `aws help` |
| `commitizen` | Commitizen | Conventional-commit prompt, semantic version bumping, and changelog generation (`devstuff configure commitizen`) | `cz --help` |
| `eza` | eza | Modern ls replacement with git status, icons, and tree view | `eza --help` |
| `gh` | GitHub CLI | GitHub's official CLI | `gh --help` |
| `htop` | htop | Interactive process and resource monitor | `man htop` |
| `mkcert` | mkcert | Zero-config local HTTPS certificates | `mkcert --help` |
| `nerd-font` | JetBrainsMono Nerd Font | Patched font supplying the icons Starship and other CLI tools draw | `fc-list \| grep -i "nerd font"` |
| `ollama` | Ollama | Run large language models locally | `ollama --help` |
| `php` | PHP 8.4 | PHP 8.4 + common extensions via ondrej/php PPA | `php --help` |
| `pi` | Pi Coding Agent | AI coding agent npm package | `pi --help` |
| `pre-commit` | pre-commit | Git hook manager for automated code quality checks | `pre-commit --help` |
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

Custom packages live in `~/.config/dev-setup/tools.yaml`. You can create them via `devstuff add`, import them with `devstuff catalog import`, or edit the YAML by hand.

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
├── dev-setup              # Bash entry point — bootstraps uv, then exec's Python
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
