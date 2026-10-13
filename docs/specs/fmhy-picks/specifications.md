# FMHY picks — specifications

Five tools and five links chosen from a scan of the FMHY wiki's docs
(<https://github.com/fmhy/edit/tree/main/docs>), mainly `developer-tools.md`, `linux-macos.md`,
`system-tools.md`, `file-tools.md` and `text-tools.md`. Selected 2026-10-13; the owner approved the
list and answered the design questions recorded in [`stack-decisions.md`](stack-decisions.md).

## Selection criteria

A candidate was ranked on:

1. **Fit.** A Linux command-line tool that one of the existing install types can install, and that
   the CI install container (`ubuntu:24.04`) can test. Desktop-only and server-stack software is a
   link, not a tool.
2. **Connection to the catalog.** It completes something devstuff already ships (a wizard, a tool
   group), rather than standing alone.
3. **Prominence.** FMHY stars it, or lists it in more than one place.
4. **Not already present.** Anything in `tools.yaml` or `links.yaml` was excluded (lazygit, bat,
   htop, starship, tmux, gum, gh, ghgrab, syncthing, Homepage, Server Box, Devhints, Linux Command
   Library, crontab.guru and so on).

## Selected tools

| Key | Tool | FMHY section | Why |
|---|---|---|---|
| `delta` | delta | Git Tools | The lazygit wizard's recommended "With delta" preset writes `delta --dark --paging=never` but cannot install delta. |
| `ripgrep` | ripgrep | CLI Tools | The standard fast code search. |
| `shellcheck` | ShellCheck | Terminal / Shell | Lints shell scripts; this project ships a lot of bash in YAML. The pre-commit wizard's shellcheck hook runs in Docker and gives no local binary. |
| `fzf` | fzf | Fuzzy finders | The usual fuzzy finder, behind shell history search and many other tools. |
| `opencode` | OpenCode | AI coding harnesses (starred) | The main open-source coding agent missing from `ai-tools` (claude-code, codex, pi, devin). |

Runners-up, not selected: lazydocker (no apt package), btop (overlaps htop), zoxide, glow, lnav, gdu.

## Selected links

| Key | Link | Category | FMHY section |
|---|---|---|---|
| `devdocs` | DevDocs — <https://devdocs.io/> | `reference` | Developer Tools (starred) |
| `archwiki` | ArchWiki — <https://wiki.archlinux.org/> | `reference` | Linux Guides (starred) |
| `explainshell` | explainshell — <https://explainshell.com/> | `shell` | Terminal / Shell |
| `awesome-selfhosted` | Awesome Selfhosted — <https://awesome-selfhosted.net/> | `discovery` | Server / Selfhosting (starred) |
| `deps-dev` | deps.dev — <https://deps.dev/> | `security` | Developer Tools |

Runners-up, not selected: Free for Developers, Can I Use, Compiler Explorer, TerminalTrove, grep.app.

## Requirements

### Tools

- **FR-1** `delta`: `type: apt`, `apt_packages: git-delta`, `check_cmd: delta`. The package and the
  command have different names; the check names the command.
- **FR-2** `ripgrep`: `type: apt`, `apt_packages: ripgrep`, `check_cmd: rg`.
- **FR-3** `shellcheck`: `type: apt`, `apt_packages: shellcheck`, `check_cmd: shellcheck`.
- **FR-4** `fzf`: `type: bash`. The install script installs the `fzf` apt package and appends a
  marked block to `~/.bashrc` that turns on fzf's key bindings (Ctrl-R, Ctrl-T, Alt-C). The remove
  script removes the package and strips exactly that block.
  - **FR-4a** The block must work on fzf 0.44 (Ubuntu 24.04, no `fzf --bash`) and on fzf ≥ 0.48:
    use `fzf --bash` when it succeeds, otherwise source
    `/usr/share/doc/fzf/examples/key-bindings.bash` if it is readable, otherwise do nothing.
  - **FR-4b** The block must never make a shell's startup fail or print an error, including when
    fzf has since been uninstalled by other means.
  - **FR-4c** Re-running install must not add a second block.
- **FR-5** `opencode`: `type: npm`, `npm_name: opencode-ai`, `check_cmd: opencode`, requires `nvm`
  (the npm type's default).
- **FR-6** Every new tool has a CI install-matrix entry in `.github/workflows/test-installs.yml`
  and a row in the README's tool table.

### lazygit wizard

- **FR-7** When the user picks a delta pager and `delta` is not on `PATH`, the wizard offers to
  install the `delta` catalog tool through `install_by_key`, the same way the starship wizard
  offers `nerd-font`. Declining keeps the current warning and continues; the chosen pager is not
  changed.
- **FR-8** The offer is made only for a pager whose command is `delta`. Any other missing pager
  command keeps today's warning with no offer.
- **FR-9** If the install fails or is declined, the warning tells the user how to install later:
  `devstuff install delta`.

### Links

- **FR-10** The five links above are added to `links.yaml` under existing categories.

## Non-goals

- Installing a tool does not change its configuration. In particular, `delta` does not set git's
  `core.pager`; that belongs to a git configurator (see the unscheduled `git-config/` item in
  `docs/ROADMAP.md`).
- No newer-than-archive builds. All four apt tools use the Ubuntu archive even where upstream is
  ahead (see `stack-decisions.md`).
- No fzf integration for shells other than bash, matching the starship and homebrew entries.
