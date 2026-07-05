# dev-setup

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
| `git` | `git`-type custom packages (`dev-setup add` → git) |
| `node` / `npm` | `npm`-type custom packages |
| `uv` | Running from source via `./dev-setup` (auto-installed if missing) |

---

## Installation

### From PyPI (recommended)

The simplest install — no git clone required, Python 3.11+ is the only prerequisite:

```bash
# pipx gives the tool its own isolated environment (preferred)
pipx install dev-setup

# or plain pip
pip install dev-setup
```

After install, `dev-setup` is available as a command. Run `dev-setup --help` to verify.

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
dev-setup list
```

---

## How it works

When installed from PyPI (via `pip` or `pipx`), the `dev-setup` command is a standard Python entry point — Python is the only runtime dependency. The `[project.scripts]` entry in `pyproject.toml` maps `dev-setup` directly to `dev_setup.__main__:main`.

The bash `./dev-setup` script in the repo is a convenience runner for the git-clone workflow. It creates a `.venv` using `python3 -m venv` (falling back to `uv venv` on systems where `python3-venv` is a separate package) and installs the project in editable mode on first run.

---

## Commands

### `list`

Show all available packages with their install status, type, version, and help command.

```bash
dev-setup list                    # all packages
dev-setup list core               # core category only
dev-setup list tools              # tools category only
dev-setup list custom             # custom/user-added packages only
dev-setup list --installed        # only installed packages
dev-setup list --available        # only packages not yet installed
```

Output columns: status (✔/✘), package key, description, install type, version (if installed), help command.

---

### `install`

Install one or more packages by key, or launch an interactive multi-select picker.

```bash
dev-setup install docker nvm      # install specific packages
dev-setup install                 # interactive picker (Space to toggle, Enter to confirm)
```

The interactive picker shows all available packages with their current install status and lets you select multiple at once before confirming.

---

### `remove`

Uninstall an installed package. Always asks for confirmation before proceeding.

```bash
dev-setup remove htop
dev-setup uninstall htop          # alias
```

---

### `update`

Update one or more already-installed packages to the latest version, or pin a single package to
a specific version with `--version`. With no arguments, launches an interactive multi-select
picker over installed packages, similar to `install`.

```bash
dev-setup update nvm                    # update to latest
dev-setup update pi --version 1.2.3     # pin a single package to a specific version
dev-setup update                        # interactive picker
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
`sudo` runs again), since there's no narrower update mechanism available. `dev-setup update` asks
for confirmation before doing this.

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
dev-setup add
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
dev-setup delete my-tool
dev-setup rm my-tool              # alias
```

Asks for confirmation, then removes the entry from `~/.config/dev-setup/tools.yaml`.

---

### `catalog`

Manage the user YAML catalog.

```bash
dev-setup catalog path                 # print ~/.config/dev-setup/tools.yaml
dev-setup catalog export               # write ./dev-setup-tools.yaml
dev-setup catalog export tools.yaml    # write effective catalog to a path
dev-setup catalog import tools.yaml    # validate and merge into user catalog
```

The effective catalog is loaded in this order:

1. Bundled tools from `src/dev_setup/tools.yaml`
2. Legacy JSON migration from `~/.config/dev-setup/packages/*.json`
3. User overrides and additions from `~/.config/dev-setup/tools.yaml`

When a user key matches a bundled key, the user definition overrides the bundled definition in place. New user keys are appended after bundled tools.

---

## Functions/Scripts

Reusable shell functions/snippets, tracked in a separate catalog from installable tools
(`~/.config/dev-setup/functions.yaml`, same bundled+user precedence merge as `tools.yaml`).
Unlike tools, functions aren't installed/removed — they're invoked.

There are two function `type`s, because a `dev-setup` command runs as its own child process
and can't mutate the shell that invoked it:

| Type | What it does | How you invoke it |
|------|---------------|--------------------|
| `script` | Runs as a subprocess (like a tool's `install_script`) — for anything that just calls other binaries/apps and doesn't need to change your shell's state. | `dev-setup run <key> [args...]` — prompts for any missing required param. |
| `shell-eval` | For things that must mutate the *calling* shell — env vars, `cd`, aliases, agents. Has two `register` modes (see below). | Depends on `register`. |

`shell-eval` functions declare `register`:

- **`register: bashrc`** (default) — `dev-setup functions enable <key>` patches a real shell
  function into `~/.bashrc` (idempotent, using the same patch/remove mechanism as tool
  bashrc blocks). After enabling, open a new shell (or `source ~/.bashrc`) and call the
  function directly by name — `dev-setup` itself never runs it, since a child process
  can't export environment changes back to your interactive shell.
  ```bash
  dev-setup functions enable ssh-agent-key
  source ~/.bashrc
  ssh-agent-key ~/.ssh/id_ed25519
  ```
  `dev-setup functions disable <key>` removes it from `~/.bashrc`.
- **`register: eval`** — `dev-setup run <key> [args]` resolves params and prints shell code
  to stdout only (no prompts, no formatting — anything else on stdout would corrupt the
  `eval` capture); missing required params are reported on stderr and exit non-zero instead.
  ```bash
  eval "$(dev-setup run some-eval-function arg1)"
  ```

Other commands:

```bash
dev-setup functions list      # show all functions, their type, and declared params
dev-setup functions path      # print ~/.config/dev-setup/functions.yaml
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
| `required` | no | Defaults to `true`. Whether the param must resolve to a non-empty value. An explicitly empty value (`dev-setup run key ""`) counts as missing, same as not passing it at all. |
| `default` | no | Defaults to `""`. Fallback value used when nothing else resolves it. A required param *with* a default is always satisfied by it, so it never triggers a resolution error or (for `register: bashrc`) the runtime bash guard described below. |

Unknown fields fail validation, same as tools. A required param without a default behaves
differently per invocation path:
- `type: script` — prompts for it interactively (unless stdin isn't a terminal, in which case
  it's reported and the command exits non-zero rather than hitting an unreadable prompt).
- `register: eval` — reported on stderr and exits non-zero; never prompts, to keep stdout
  clean for `eval` capture.
- `register: bashrc` — `dev-setup` is never involved when the enabled function is called
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
| `eza` | eza | Modern ls replacement with git status, icons, and tree view | `eza --help` |
| `gh` | GitHub CLI | GitHub's official CLI | `gh --help` |
| `htop` | htop | Interactive process and resource monitor | `man htop` |
| `mkcert` | mkcert | Zero-config local HTTPS certificates | `mkcert --help` |
| `ollama` | Ollama | Run large language models locally | `ollama --help` |
| `php` | PHP 8.4 | PHP 8.4 + common extensions via ondrej/php PPA | `php --help` |
| `pi` | Pi Coding Agent | AI coding agent npm package | `pi --help` |
| `saml2aws` | saml2aws | SAML → AWS STS credentials CLI (Versent) | `saml2aws --help` |
| `starship` | Starship | Fast, cross-shell customizable prompt | `starship --help` |
| `yq` | yq | Portable command-line YAML/JSON/XML processor | `yq --help` |

### Languages

| Key | Name | Description | Help |
|-----|------|-------------|------|
| `go` | Go | Go programming language toolchain | `go help` |
| `java` | Java 21 (OpenJDK) | OpenJDK 21 LTS - JDK and JRE | `java --help` |
| `ruby` | Ruby (rbenv) | Ruby via rbenv version manager + ruby-build | `ruby --version` |

---

## Custom packages

Custom packages live in `~/.config/dev-setup/tools.yaml`. You can create them via `dev-setup add`, import them with `dev-setup catalog import`, or edit the YAML by hand.

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
| `docs_url` | no | URL opened by `dev-setup docs <key>` |
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
├── install.sh             # Symlinks dev-setup into ~/.local/bin
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
        │   └── functions_cmd.py
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
