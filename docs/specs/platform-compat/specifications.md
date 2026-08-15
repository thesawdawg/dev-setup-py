# Platform compatibility layer — specifications

Status: complete (v1) — 2026-08-15

## Problem statement

`devstuff` assumed exactly one host shape: a Debian/Ubuntu box with `sudo`, `apt-get`, and the
FHS. That assumption was not written down anywhere; it was spread across `generic.py`
(`["sudo", "apt-get", "install", "-y", …]`, `dpkg -s`) and every `install_script` in
`tools.yaml` (`sudo mv … /usr/local/bin`, `add-apt-repository`, `systemctl enable`).

Termux is the case that makes this visible, because it breaks the assumption in four independent
ways at once rather than one. But the fix is not "add a Termux branch" — Fedora, Arch, Alpine and
macOS each break a different subset of the same assumptions, and were failing silently in exactly
the same way. What was missing was a place to *ask* what the host is.

## Measured findings

Established 2026-08-15 against primary sources — the `termux-tools` and `termux-packages` trees —
rather than from recollection. Each is cited, because several contradict the plausible guess.

### Termux

- **F-1. `pkg` is a wrapper over two different package managers.**
  [`termux-tools/scripts/pkg.in`](https://github.com/termux/termux-tools/blob/master/scripts/pkg.in)
  dispatches on `$TERMUX_APP_PACKAGE_MANAGER`, which is either `apt` or `pacman`. Both builds are
  shipped. `pkg install` → `apt install "$@"` or `pacman -Sy --needed "$@"`; `pkg uninstall` →
  `apt remove` or `pacman -Rcns`. So a layer that hardcodes "Termux means apt" is already wrong.
- **F-2. `pkg` refuses to run as root.** The script's first statement is
  `if [[ "$(id -u)" == "0" ]]; then echo "Error: Cannot run 'pkg' command as root"; exit 1; fi`.
  A root Termux session therefore has to talk to `apt`/`pacman` directly, losing mirror selection.
- **F-3. `pkg install` already refreshes.** It calls `select_mirror` and `update_apt_cache`
  itself (the latter runs `apt update` only when the cache is stale or the sources file is newer).
  A separate `pkg update` before every install would re-run the whole mirror probe for nothing.
- **F-4. Termux's `sudo` is not sudo.** The `sudo` package is
  [agnostic-apollo/sudo](https://github.com/termux/termux-packages/blob/master/packages/sudo/build.sh),
  "a wrapper script to drop to the supported shells … as the root user", conflicting with `tsu`.
  It requires a rooted device. `$PREFIX` is owned by the app user, so there is nothing to escalate
  to in the first place — and `shutil.which("sudo")` finding it must not be read as "sudo works".
- **F-5. The package-manager backend is discoverable from the environment.**
  [`termux-setup-package-manager`](https://github.com/termux/termux-tools/blob/master/scripts/termux-setup-package-manager.in)
  reads `$TERMUX_APP_PACKAGE_MANAGER` (exported by termux-app v0.119.0+), falling back to
  `$TERMUX_MAIN_PACKAGE_FORMAT` (`debian`/`pacman`) on older builds. Detection follows the same
  chain so devstuff and `pkg` never disagree about which backend is in play.
- **F-6. Far more of the catalog exists as native Termux packages than expected.** Verified by
  fetching `packages/<name>/build.sh` from termux-packages: `uv` (built from source, 0.12.5),
  `python`, `nodejs-lts`, `golang`, `openjdk-21`, `gh`, `git-lfs`, `lazygit`, `starship`, `bat`,
  `htop`, `yq`, `php`, `ruby`, and — the two surprises — **`awscli` (AWS CLI v2, built from
  source)** and **`ollama`** (built with the Vulkan backend, aarch64/x86_64 only). Absent:
  `docker`, `nvm`, `mkcert`, `saml2aws`, `ansible`.
- **F-7. `termux-fix-shebang` exists because `#!/bin/bash` does not.**
  It rewrites `#!*/bin/x` to `#!$PREFIX/bin/x`. devstuff is unaffected — `_run_bash_script` runs
  `bash <file>`, resolving `bash` through PATH — but it confirms that any script *written* to disk
  with an absolute interpreter path is a portability hazard here.
- **F-8. uv's managed interpreters cannot work on Termux.** They come from
  python-build-standalone, which targets linux-gnu, linux-musl, macOS and Windows — never Bionic.
  Left at its default preference uv attempts a download and reports the failure as if the
  *package* were unavailable.

### Other hosts

- **F-9. The `glibc` requirement is not the same as "not Termux".** Alpine is musl, and the
  release assets `bat` selects (`*-unknown-linux-gnu`) fail there for the same reason. Modelling
  this as a host *capability* rather than a platform *name* made Alpine correct for free.
- **F-10. Several tools are Debian-specific in a way no package manager abstraction fixes.**
  `gh` adds an apt keyring and a `sources.list.d` entry; `php` adds `ppa:ondrej/php`; `ruby`
  installs Debian-named build dependencies. These are not "install X" — they are "install X the
  Debian way", and on Fedora they failed *after* writing files.
- **F-11. A root container often has no `sudo` at all.** The pre-existing
  `["sudo", "apt-get", …]` turned every system install into `sudo: command not found` there.

## Functional requirements

### Detection

- **FR-1.** A single module (`platforms.py`) answers what host devstuff is on, cached per
  process, never re-derived at call sites.
- **FR-2.** Termux is detected from `$PREFIX` having the `…/files/usr` shape *and* a corroborating
  Termux signal (`$TERMUX_VERSION`, `$TERMUX__ROOTFS`, or a `com.termux` path), or from the
  default prefix existing on disk. `$PREFIX` alone is never sufficient — it is an ordinary build
  variable. (F-2 of the risk list; see `test_a_bare_prefix_variable_is_not_termux`.)
- **FR-3.** The Termux package-manager backend is resolved by F-5's chain, and a root session
  bypasses the `pkg` wrapper entirely per F-2.
- **FR-4.** On other Linux hosts the family comes from `/etc/os-release` (`ID`, then `ID_LIKE`),
  and the package manager is confirmed on PATH. If os-release is missing or names a family whose
  manager is not installed, a PATH probe decides instead.
- **FR-5.** Detection never raises. The worst case is a platform with no package manager, which
  degrades to "system-type tools unavailable, everything else works".
- **FR-6.** `$DEVSTUFF_PLATFORM` forces the identity, for previewing another host's view of the
  catalog. `devstuff platform` states prominently when it is set.

### Package managers

- **FR-7.** Each manager is a record of argv fragments (`PackageManager`), covering install,
  remove, upgrade, index refresh and installed-state query. Supported: apt-get, Termux `pkg`
  (both backends) and its root fallbacks, dnf, yum, pacman, apk, zypper, brew.
- **FR-8.** Package names reach `subprocess` as argv elements, never through a shell.
- **FR-9.** Version pinning uses the manager's own separator (`=` apt/apk, `-` dnf/zypper) and is
  **refused up front** where the manager cannot express it (pacman, brew) or where more than one
  package is named — rather than silently installing latest.
- **FR-10.** A manager that has no separate refresh command (Termux `pkg`, per F-3) declares
  none, and no refresh is attempted.
- **FR-11.** Installed-state queries use each manager's own tool (`dpkg -s` + status marker,
  `rpm -q`, `pacman -Q`, `apk info -e` + name marker, `brew list --versions`).

### Escalation

- **FR-12.** `escalate(cmd)` prefixes `sudo` only where the host both needs and has it. It is
  skipped when already root (F-11), on Termux (F-4), and for managers that refuse to run under
  sudo (Homebrew).
- **FR-13.** Termux never carries the `sudo` capability regardless of what is on PATH (F-4).

### Catalog schema

- **FR-14.** New type `system` (canonical) with field `packages`, meaning "install via the host's
  package manager". The existing `apt` type and `apt_packages` field remain first-class aliases —
  user catalogs are full of them — and dispatch to the identical handlers.
- **FR-15.** `requires_traits:` lists host capabilities an entry's install mechanism needs
  (`glibc`, `fhs`, `sudo`, `systemd`, `apt`, `android`). Where a trait is absent the tool is
  marked unavailable with a generated explanation. An unknown trait name is a load-time error —
  a typo would otherwise make the tool permanently unavailable everywhere, with wording the user
  cannot act on.
- **FR-16.** `platforms:` maps a platform id or family to an override block, which may either
  mark the tool `supported: false` with a mandatory `reason`, or replace any tool field for that
  host. Blocks do not nest.
- **FR-17.** Precedence is base → `platforms.<family>` → `platforms.<id>`. Merging is literal per
  key.
- **FR-18.** Because merging is literal, an override that touches any install-mechanism field
  while the base declares `requires_traits` **must restate `requires_traits`**. Omitting it is a
  load-time error. (Without this rule, an override written specifically to support a platform
  would inherit the trait requirements of the mechanism it just replaced and report itself
  unavailable there — a silent failure of exactly the thing being added.)
- **FR-19.** `supported: false` without a `reason` is a load-time error. An unexplained refusal is
  worse than a failed install: the user is told no and given nothing to do about it.
- **FR-20.** Resolution happens in the registry, not the catalog loader. `catalog export` and the
  user's own YAML keep their `platforms:` blocks intact; only runtime objects are host-specific.
- **FR-21.** `unsupported_reason` and `alternative` are never written back to a catalog file.

### Scripts

- **FR-22.** Every `install_script`/`remove_script` body is prefixed with a platform prelude
  exporting `DEVSTUFF_PLATFORM`, `DEVSTUFF_OS_FAMILY`, `DEVSTUFF_SUDO`, `DEVSTUFF_PREFIX`,
  `DEVSTUFF_BIN`, `DEVSTUFF_PKG`, `DEVSTUFF_PKG_INSTALL`, `DEVSTUFF_PKG_REMOVE`.
- **FR-23.** Every prelude variable is always set (scripts run under `set -u`), and
  `DEVSTUFF_SUDO` is the empty string where there is nothing to escalate to, so `$DEVSTUFF_SUDO cmd`
  degrades to `cmd`.
- **FR-24.** The prelude is inserted *after* a leading shebang, so the first line stays first.
- **FR-25.** A platform may declare environment applied to every child process. Termux sets
  `UV_PYTHON_DOWNLOADS=never` (F-8).

### Refusal and reporting

- **FR-26.** `GenericTool.install()` and `.update()` refuse an unsupported tool before running
  anything, with the reason and — where one exists — the alternative catalog key. Enforced in the
  engine, not only the command layer, because configurators and the agent bridge also install.
- **FR-27.** `devstuff install` reports unavailability *before* missing requires: prerequisites
  are irrelevant when the tool itself cannot run.
- **FR-28.** `devstuff list` marks unavailable entries and shows the reason; the interactive
  picker disables them.
- **FR-29.** `devstuff platform` shows the detection result, capabilities present and absent, and
  which catalog entries are unavailable here and why. `--json` emits the same data.
- **FR-30.** `devstuff doctor` gains a `platform` check: PASS when recognised with a manager on
  PATH; WARN when there is no manager (saying what still works), when the manager is missing from
  PATH, or when the platform is unrecognised.

## Non-functional requirements

- **NFR-1.** No new runtime dependencies. Detection is `os.environ`, `/etc/os-release` and
  `shutil.which`.
- **NFR-2.** Behaviour on Debian/Ubuntu is unchanged, including argv, apart from dropping `sudo`
  when already root (FR-12).
- **NFR-3.** Detection cost is a few file stats; it must not add measurable time to `list`.

## Out of scope

- Windows and WSL-specific handling (WSL is detected as its underlying distro, which is correct).
- `proot-distro` — running a Debian userland under Termux is a different host, and devstuff
  inside it correctly detects Debian.
- Rewriting every `install_script` to be portable. Scripts stay host-specific; the traits and
  overrides say *where* they apply. The prelude exists so new ones need not be.
- A `configure`-style wizard for authoring `platforms:` blocks in `devstuff add`.
- Per-platform `functions.yaml` and `agent_tools.yaml` gating.

## Open questions

- **Should `apt`/`apt_packages` eventually be deprecated in favour of `system`/`packages`?**
  Deferred. They are aliases with identical behaviour, so there is no correctness pressure, and a
  deprecation warning would fire on user catalogs the user cannot be expected to migrate on our
  schedule.
- **Should `requires_traits` be inferred from script contents?** Rejected for v1 — grepping a
  script body for `sudo` to guess its requirements is exactly the kind of inference that is right
  90% of the time and impossible to debug the other 10%.
