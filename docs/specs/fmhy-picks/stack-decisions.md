# FMHY picks — stack decisions

## Measured package versions (2026-10-13)

From packages.ubuntu.com and npm:

| Package | `noble` (24.04, the CI image) | `resolute` (dev host) | Upstream |
|---|---|---|---|
| `git-delta` (installs `/usr/bin/delta`) | 0.16.5 | 0.18.2 | 0.20.1 |
| `ripgrep` | 14.1.0 | 15.1.0 | — |
| `shellcheck` | 0.9.0 | 0.11.0 | — |
| `fzf` | 0.44.1 | 0.67.0 | 0.74.5 |
| npm `opencode-ai` | — | — | 1.19.0, MIT, `bin: opencode`, has a `postinstall` |

None of the five is installed on the dev host.

## Decisions

### D-1: the Ubuntu archive for delta, ripgrep, ShellCheck and fzf

**Chosen:** `apt` from the distro archive (`type: bash` for fzf, see D-3).

**Rejected:** upstream GitHub release binaries. Each is newer, but needs a per-tool download
script with architecture mapping, version discovery and checksum handling, plus a hand-written
updater. The archive versions are all usable: delta 0.16.5 already supports `--dark` and
`--paging=never`, which is what the lazygit preset writes. `type: apt` also gives `outdated`,
`update` and version pinning for free.

### D-2: the lazygit wizard offers to install delta

**Chosen** (owner, 2026-10-13): when a delta pager is selected and `delta` is missing, offer to
install it through `install_by_key` (`commands/install_cmd.py`), the existing seam the starship
wizard uses for `nerd-font`.

**Rejected:** catalog entry only, leaving the wizard's warning as the only help.

### D-3: fzf as a `type: bash` entry with a `~/.bashrc` block

**Chosen** (owner, 2026-10-13): a bash script that installs the apt package and appends a marked
block, with a remove script that strips it. Same pattern as `starship` and `homebrew`.

**Cost, accepted:** a `bash` entry has no update checker, so `devstuff outdated` reports fzf as
`unsupported`, `devstuff update fzf` is a full reinstall, and fzf cannot be pinned in a profile.

**Cost, accepted (owner, 2026-10-13):** the check requires the marker in `~/.bashrc` (FR-4d),
so a user whose own fzf setup already binds the keys sees fzf as *not installed* — and
`devstuff remove fzf` would apt-remove an fzf they may have installed themselves.

**Rejected:** extending the `apt` type to run `install_script` after `apt-get install`. It would
keep fzf's update check, but is an engine change. **Finding while deciding:** today an `apt` entry
honours `remove_script` but silently ignores `install_script`, and `validate_catalog` does not
reject it. That trap is recorded here, not fixed by this work.

**Why the block has a fallback (FR-4a):** `fzf --bash` was added in fzf 0.48, and the CI image's
fzf is 0.44. Both noble's and resolute's `fzf` packages ship
`/usr/share/doc/fzf/examples/key-bindings.bash`. Minimized Docker images exclude `/usr/share/doc`,
so in the CI container the block may legitimately do nothing; the install test checks the binary,
not the key bindings.

### D-4: OpenCode through the `npm` type

**Chosen** (owner kept OpenCode over the runners-up, 2026-10-13): `npm_name: opencode-ai`, like
`claude-code` and `codex`.

**Rejected:** OpenCode's `curl | bash` installer, which would make it a `bash` entry with no update
check. Note that the package's real binary comes from its `postinstall`; the generic npm installer
runs scripts (only the `pi` entry passes `--ignore-scripts`, in its own script), so nothing extra is
needed.

### D-5: link categories

All five go into existing categories (`reference`, `shell`, `discovery`, `security`). explainshell
sits under `shell` next to the tmux wiki and Linux Command Library rather than `reference`, because
it explains shell commands specifically.
