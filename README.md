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

### `add`

Guided wizard to register a new custom package. Supports six install types:

| Type | What it does |
|------|-------------|
| `npm` | `npm install -g <package>` |
| `pip` | `uv tool install <package>` (falls back to `pip3 install --user`) |
| `apt` | `sudo apt-get install -y <packages>` |
| `git` | `git clone --depth=1 <url>` with optional post-clone and pre-remove commands |
| `script` | `curl -fsSL <url> \| sh` — single-URL convenience script |
| `bash` | Arbitrary multi-step bash — opens `$EDITOR` for install and remove scripts |

```bash
dev-setup add
```

The wizard collects type-specific fields, then prompts for a help command (e.g. `tool --help`). Packages are saved as JSON files in `~/.config/dev-setup/packages/`.

#### `bash` type

For tools like AWS CLI or saml2aws that require multiple download/extract/install steps, choose the `bash` type. The wizard opens `$EDITOR` twice — once for the install script and once for the optional remove script — with a `#!/usr/bin/env bash / set -euo pipefail` template pre-filled.

Example JSON for a `bash`-type custom package:

```json
{
  "name": "batcat",
  "description": "Modern cat with syntax highlighting",
  "category": "custom",
  "type": "bash",
  "check_cmd": "bat",
  "help_cmd": "bat --help",
  "install_script": "set -euo pipefail\nVER=$(curl -s https://api.github.com/repos/sharkdp/bat/releases/latest | grep tag_name | cut -d'\"' -f4 | sed 's/v//')\ncurl -fsSL \"https://github.com/sharkdp/bat/releases/download/v${VER}/bat_${VER}_amd64.deb\" -o /tmp/bat.deb\nsudo dpkg -i /tmp/bat.deb && rm /tmp/bat.deb",
  "remove_script": "sudo dpkg -r bat"
}
```

---

### `delete`

Remove a custom package from the registry. Built-in packages cannot be deleted.

```bash
dev-setup delete my-tool
dev-setup rm my-tool              # alias
```

Asks for confirmation, then deletes the JSON file from `~/.config/dev-setup/packages/`.

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
| `htop` | htop | Interactive process and resource monitor | `man htop` |
| `php` | PHP 8.4 | PHP 8.4 + common extensions via ondrej/php PPA | `php --help` |
| `saml2aws` | saml2aws | SAML → AWS STS credentials CLI (Versent) | `saml2aws --help` |
| `starship` | Starship | Fast, cross-shell customizable prompt | `starship --help` |

---

## Custom packages

Custom packages live in `~/.config/dev-setup/packages/` as JSON files. Each file is named `<key>.json`. You can create them via `dev-setup add` or write them by hand.

### JSON fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Display name shown in `list` |
| `description` | no | Short description shown in `list` |
| `category` | no | `custom` (default), `core`, or `tools` |
| `type` | yes | `npm`, `pip`, `apt`, `git`, `script`, or `bash` |
| `check_cmd` | no | Binary name checked with `which` to detect install status |
| `help_cmd` | no | Command shown in `list` under the package entry |
| `npm_name` | npm | npm package name |
| `pip_name` | pip | PyPI package name |
| `apt_packages` | apt | Space-separated list of apt packages |
| `git_url` | git | Repository URL to clone |
| `git_install_cmd` | git | Bash command run inside the cloned repo after clone |
| `git_remove_cmd` | git | Bash command run inside the repo before deletion |
| `script_url` | script | URL passed to `curl -fsSL … \| sh` |
| `install_script` | bash | Full bash script to run on install |
| `remove_script` | bash | Full bash script to run on remove |

### Examples

**npm package:**
```json
{
  "name": "Prettier",
  "description": "Opinionated code formatter",
  "type": "npm",
  "npm_name": "prettier",
  "check_cmd": "prettier",
  "help_cmd": "prettier --help"
}
```

**pip package:**
```json
{
  "name": "httpie",
  "description": "Human-friendly HTTP client",
  "type": "pip",
  "pip_name": "httpie",
  "check_cmd": "http",
  "help_cmd": "http --help"
}
```

**apt package:**
```json
{
  "name": "ripgrep",
  "description": "Fast recursive search tool",
  "type": "apt",
  "apt_packages": "ripgrep",
  "check_cmd": "rg",
  "help_cmd": "rg --help"
}
```

**Multi-step bash install:**
```json
{
  "name": "saml2aws (custom)",
  "description": "SAML-to-AWS credential helper",
  "type": "bash",
  "check_cmd": "saml2aws",
  "help_cmd": "saml2aws --help",
  "install_script": "set -euo pipefail\nVER=$(curl -s https://api.github.com/repos/Versent/saml2aws/releases/latest | grep tag_name | cut -d'v' -f2 | cut -d'\"' -f1)\ncurl -fsSL \"https://github.com/Versent/saml2aws/releases/download/v${VER}/saml2aws_${VER}_linux_amd64.tar.gz\" | tar -xz -C /tmp\nsudo mv /tmp/saml2aws /usr/local/bin/saml2aws\nsudo chmod +x /usr/local/bin/saml2aws",
  "remove_script": "sudo rm -f /usr/local/bin/saml2aws"
}
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
        ├── registry.py    # Auto-discovers Tool subclasses via pkgutil, loads custom JSON
        ├── generic.py     # GenericTool — handles all 6 custom install types
        ├── ui.py          # Rich console helpers, questionary wrappers, styled prompts
        ├── commands/
        │   ├── list_cmd.py
        │   ├── install_cmd.py
        │   ├── remove_cmd.py
        │   ├── add_cmd.py
        │   └── delete_cmd.py
        └── packages/      # Built-in Tool subclasses — one file per tool
            ├── docker.py
            ├── nvm.py
            ├── uv_tool.py
            ├── aws_cli.py
            ├── saml2aws.py
            ├── php.py
            ├── starship.py
            └── htop.py
```

### Adding a new built-in tool

Create one file in `src/dev_setup/packages/`. The registry auto-discovers any class that subclasses `Tool` and has a non-empty `key` — no registration arrays to update.

```python
# src/dev_setup/packages/my_tool.py
import shutil, subprocess
from typing import Optional
from dev_setup.base import Tool

class MyTool(Tool):
    key         = "mytool"
    name        = "My Tool"
    description = "Does something useful"
    category    = "tools"          # "core", "tools", or "custom"
    install_type = "script"
    help_cmd    = "mytool --help"

    def is_installed(self) -> bool:
        return shutil.which("mytool") is not None

    def get_version(self) -> str:
        r = subprocess.run(["mytool", "--version"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    def install(self) -> Optional[str]:
        from dev_setup import ui
        with ui.spinner("Installing My Tool..."):
            subprocess.run(["bash", "-c", "curl -fsSL https://example.com/install.sh | sh"],
                           check=True, capture_output=True)
        if not self.is_installed():
            raise RuntimeError("mytool binary not found after install")
        return self.get_version()

    def remove(self) -> None:
        from dev_setup import ui
        with ui.spinner("Removing My Tool..."):
            subprocess.run(["sudo", "rm", "-f", "/usr/local/bin/mytool"],
                           check=True, capture_output=True)
```

### Key design decisions

- **uv owns Python provisioning.** The bash wrapper only guarantees uv is present; Python version and virtualenv management is delegated entirely to `uv run`.
- **Registry is auto-discovery.** `pkgutil.iter_modules` scans `packages/` for `Tool` subclasses — adding a built-in is a single file drop-in.
- **Custom packages are plain JSON.** No executable files in the registry; scripts are stored as strings and written to a temp file at install time, giving bash full parsing fidelity.
- **`install()` raises on failure.** Tools raise `RuntimeError` or `subprocess.CalledProcessError`; command handlers catch and report them. No `InstallResult` enum to check.
- **UI is import-isolated.** Package classes do `from dev_setup import ui` inside method bodies, keeping `is_installed()` and `get_version()` side-effect free and testable without terminal output.

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `click` | ≥ 8.1 | CLI command dispatch, `--help` generation, editor integration |
| `rich` | ≥ 13.0 | Terminal UI — panels, tables, spinners, styled text |
| `questionary` | ≥ 2.0 | Interactive prompts — multi-select, confirm, text input |
