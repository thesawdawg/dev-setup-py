# Platform compatibility layer — development plan

Status: complete (v1) — 2026-08-15

## Milestones

| # | Milestone | State |
|---|-----------|-------|
| M1 | Research Termux against primary sources (termux-tools, termux-packages) | done — findings F-1…F-8 |
| M2 | `platforms.py`: `Platform`, `PackageManager`, detection, escalation, prelude | done |
| M3 | Catalog schema: `packages`, `requires_traits`, `platforms:`, validation, resolution | done |
| M4 | `generic.py`: `system` type, platform-driven install/remove/update/query, prelude, refusal | done |
| M5 | CLI surface: `devstuff platform`, install/list guards, `doctor` check, `add` wizard | done |
| M6 | Catalog data: traits on every bundled entry, Termux overrides, htop conversion | done |
| M7 | Tests, spec, README/CLAUDE.md | done |

## Testing strategy

Unit tests only (`tests/test_platforms.py`, 60 cases) plus extensions to `test_generic.py` and
`test_doctor.py`. There is no Termux in CI and there will not be one, so the tests are built on
two things instead:

1. **Platform objects are constructible.** `platforms.set_current(termux())` makes every
   downstream behaviour — argv, escalation, prelude, catalog resolution — testable on any host
   with no mocking of the filesystem or subprocess. This is why `Platform` is a plain dataclass
   and why `set_current`/`reset` exist.
2. **Detection is tested against the *inputs*, not the outcome.** Each Termux finding has a test
   named after it: the pacman backend (F-1), the root bypass (F-2), the legacy
   `TERMUX_MAIN_PACKAGE_FORMAT` fallback (F-5), and — the one most likely to bite in the wild —
   that a bare `$PREFIX` on an ordinary Linux box is *not* Termux.

Three tests assert properties of the bundled catalog rather than of code, because that is where
the mistakes will be:

- `test_bundled_catalog_resolves_on_every_known_platform` — every entry builds a valid
  `GenericTool` on all seven platforms, and any `system`-type result actually names packages.
- `test_every_termux_override_names_a_real_mechanism` — an override either marks the tool
  unsupported with a reason, or supplies the field its resolved type requires. Catches a block
  that swaps `type` and forgets the package name.
- `test_no_termux_override_leaves_a_sudo_apt_script_live` — the hazard `remove_script: ""` guards
  against. An override can swap `type` to `system` and look complete while the base
  `remove_script` still runs `sudo apt-get remove` at uninstall time. The test only inspects
  scripts the *resolved type* will actually execute, since merging leaves dead fields behind.

### What is deliberately not tested

Real installs on Termux. The integration suite (`dev/make run-tests`) is Docker/Ubuntu and stays
that way. What can be verified off-device is that the right command *would* be run, which is what
`test_system_install_uses_the_host_manager` asserts for both Termux and Debian.

### Manual verification performed

`DEVSTUFF_PLATFORM=termux devstuff platform`, and the same for `alpine`, `fedora`, `arch`,
`macos`. The Alpine output was the check that SD-1 was the right model — ten entries correctly
excluded without a line of Alpine-specific data.

## Risks

| Risk | Mitigation |
|------|------------|
| **False-positive Termux detection** on a host that exports `$PREFIX`. Would route every system install through a nonexistent `pkg`. | Requires the `…/files/usr` shape *and* a Termux signal (FR-2), with a test. |
| **An override silently inheriting stale `requires_traits`**, making a tool unavailable on the platform it was written for. | Load-time error (FR-18, SD-4) rather than convention. |
| **An override swapping `type` while a base `remove_script` stays live**, running `sudo apt-get` at uninstall. | `remove_script: ""` in each such block, enforced by a catalog test. |
| **Termux package names drifting** (a package renamed or dropped upstream). | Install fails with the manager's own "no such package" error, which is actionable. Names were verified against termux-packages at time of writing; there is no automated check and adding one would mean scraping a repo index weekly. Accepted. |
| **Behaviour change on Debian** from routing through the new layer. | NFR-2: argv is identical, asserted in `test_apt_type_is_an_alias_of_system` and `test_system_install_uses_the_host_manager`. The one intended change is dropping `sudo` when already root. |
| **`_check_update_system` is Debian-only.** | It returns "unknown" elsewhere rather than guessing — the same contract `script`/`bash` types already have. Extending it per manager is deferred. |

## Deferred

- Per-manager update-availability probes (dnf `check-update`, `apk version -l '<'`, `brew outdated`).
- A `platforms:` authoring step in the `devstuff add` wizard.
- Trait gating for `functions.yaml` (several bundled functions shell out to Linux-only tools).
- Deprecating the `apt`/`apt_packages` spelling (see specifications.md, open questions).
