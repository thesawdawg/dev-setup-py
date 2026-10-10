# Platform compatibility layer — stack decisions

Status: complete (v1.1) — 2026-08-15

## SD-1. A capability model, not a platform switch

**Chosen:** tools declare host *capabilities* they need (`requires_traits: [glibc, fhs, sudo]`),
and platforms declare which they provide.

**Rejected:** `unsupported_platforms: [termux]` per tool.

The naming approach requires every tool to be re-audited every time a platform is added, and it
encodes a conclusion rather than a reason. The capability model paid for itself immediately: the
same `requires_traits: [glibc]` that excludes Termux also correctly excludes Alpine, which nobody
was thinking about, because musl and Bionic fail the same `*-unknown-linux-gnu` tarball for the
same reason. The generated explanation ("Alpine does not provide: prebuilt \*-linux-gnu binaries
run here") is also better than anything a platform list could produce.

The escape hatch — `platforms: {<id>: {supported: false, reason: …}}` — exists for the cases where
the *reason* is specific rather than structural (Docker needs kernel features Android does not
expose; that is not a "trait").

## SD-2. Traits are measured where measuring is possible

`systemd` is `/run/systemd/system` existing, not "the distro is Fedora". `sudo` is
`shutil.which("sudo")` or already being root. The package manager is confirmed on PATH before
being believed, and os-release is overruled by a PATH probe when it disagrees (FR-4) — minimal
container images routinely claim `ID=debian` with no apt installed.

Two traits are asserted rather than measured, and deliberately: Termux never carries `sudo`
(measuring it would find agnostic-apollo's root wrapper and get the wrong answer — F-4) and never
carries `apt` (its apt is real, but Debian repo tooling and PPAs are not). Both are commented at
the assignment.

## SD-3. `system` as a type, with `apt` as a permanent alias

**Chosen:** add `type: system` + `packages:`; keep `type: apt` + `apt_packages:` as
first-class aliases forever, dispatching to the same handlers.

**Rejected:** rename with a deprecation warning.

Catalogs are user data living in `~/.config/devstuff/tools.yaml`. A warning the user cannot
action on our schedule is noise, and the two spellings cost one dict entry each in four dispatch
tables. What *is* enforced is that an entry cannot set both `packages` and `apt_packages` — that
is a genuine ambiguity, not a compatibility concern.

The type name is the honest one: `type: apt` no longer means apt. On Fedora it is dnf, on Termux
it is `pkg`. Leaving the old name working while the new name says what it does is the least
disruptive way to end up with an accurate vocabulary.

## SD-4. Literal per-key merging, with a load-time guard

**Chosen:** override blocks merge key-wise with no special cases, *plus* a validation rule
(FR-18) that an override replacing the install mechanism must restate `requires_traits`.

**Rejected 1:** "a block that changes `type` implicitly clears `requires_traits`." Magic. A
maintainer reading the YAML cannot see it happen, and the rule has fuzzy edges (does changing
only `install_script` count?).

**Rejected 2:** plain merging with no guard. This has one specific, nasty failure: an override
written *specifically* to make a tool work on Termux inherits `requires_traits: [glibc]` from the
mechanism it replaced, and the tool reports itself unavailable on the very platform someone went
to the trouble of supporting. It is silent, it looks like the override "didn't take", and there is
nothing in the file that hints at the cause.

The guard turns that into a load error naming the tool, the scope, and the fix. Merging stays
dumb and readable; the one non-obvious interaction is the one the validator refuses to let you
get wrong.

## SD-5. Resolution in the registry, not the catalog loader

`load_effective_catalog()` returns records with `platforms:` blocks intact; `registry._load_builtins`
resolves them. This keeps `catalog export`, `catalog import` and the user's own YAML host-agnostic
— exporting a catalog on a phone and importing it on a laptop must not silently bake in the
phone's answers. Only the live `GenericTool` objects are host-specific, which mirrors how
`builtin` is already computed at registry-build time rather than stored.

## SD-6. A shell prelude instead of rewriting every script

**Chosen:** inject `DEVSTUFF_SUDO`, `DEVSTUFF_PKG_INSTALL`, `DEVSTUFF_BIN`, … ahead of every
script body, and leave existing scripts as they are.

**Rejected:** making the ~20 bundled `install_script`s portable.

Most of them cannot be made portable, because what they do is not portable: `bat`'s script
downloads a glibc tarball, `php`'s adds an Ubuntu PPA. Rewriting them would mean reimplementing
each tool's install for four package managers — which is what `type: system` is for, and where a
native package exists that is what the override now uses. For the rest, "this script is for hosts
with these traits" is the honest description, and the traits say so.

The prelude's value is forward-looking: a *new* catalog entry can be written once with
`$DEVSTUFF_SUDO` and `$DEVSTUFF_PKG_INSTALL` and work everywhere. Two bundled entries already use
it (java's Debian removal, git-lfs and starship on Termux).

`DEVSTUFF_SUDO` being the empty string rather than unset is load-bearing: scripts run under
`set -u`, and `$DEVSTUFF_SUDO cmd` has to degrade to `cmd`.

## SD-7. Refusal enforced in the engine, not the command layer

`GenericTool.install()` raises for an unsupported tool. The command layer *also* checks, to
produce nicer output — but the engine check is the control, because configurators
(`install_cmd.install_by_key` for a missing prerequisite), `update`, and the agent's catalog
bridge all reach installs without going through `devstuff install`.

## SD-8. Version pinning refused rather than approximated

`pacman` and `brew` have no way to request a specific version from their repositories.
`upgrade_argv(..., version=...)` raises there instead of quietly installing latest — the same
stance `_update_git` already took for shallow clones. A pin that silently doesn't pin is worse
than an error.

## SD-9. `$DEVSTUFF_PLATFORM` as a preview, not a shim

The override changes the platform's *identity* only. Nothing pretends the host can run those
installs, and `devstuff platform` says so in a warning. It exists so a maintainer editing Termux
overrides on a laptop can see what a Termux user will see (`DEVSTUFF_PLATFORM=termux devstuff
list`), and so tests can build a platform without mocking the filesystem.

## SD-10. Rejected: a `proot-distro` integration

Termux can run a Debian userland under `proot-distro`, and it would have been possible to detect
that and offer it as an "alternative" for tools like docker. Rejected: devstuff running *inside*
such a userland already detects Debian correctly and behaves properly, so the feature would only
be about *launching* one — a different product. And it would be a lie for docker specifically,
which proot cannot support either (no namespaces).

## SD-11. Source inspection fills the undeclared gap — and only that gap

**Chosen:** `compat.py` reads the install source, but stands down whenever the catalog said
anything about this host (`ResolvedTool.declared`).

**Rejected:** scanning everything and letting findings stack with declarations.

The two mechanisms answer the same question with different confidence. A `requires_traits` list
is a decision someone made with the tool in front of them; a regex over a shell script is a
guess. Where both exist the declaration must win, or a maintainer who deliberately wrote
`requires_traits: []` — the exact thing FR-18 forces them to write — gets overruled by a pattern
match and has no way to say "I know, it's fine".

Standing down on *any* declaration (rather than only on a trait list) also makes the rule easy to
state: say something about the platform and you own the answer; say nothing and devstuff will
read your script.

## SD-12. Two severities, chosen from the false-positive cost

A missed incompatibility leaves behaviour exactly where it was before the module existed. A false
one blocks an install that works. The asymmetry is the whole design:

- **Blocking** is reserved for things that cannot work: a command not on `$PATH` (measured with
  `shutil.which`, not inferred), a path that does not exist on this host, a glibc asset on a
  non-glibc host.
- **Advisory** covers signals that usually degrade gracefully. `systemctl` is the case that
  forced the split — `systemctl enable x 2>/dev/null || true` appears in perfectly good
  installers, and half the bundled catalog would have been blocked in a container.

## SD-13. `--force` overrides inference, never declaration

A heuristic with no escape hatch is a trap. But `supported: false` with a written reason is not a
heuristic, and "force" there would just mean "run the thing the author already told you cannot
work". So the flag is scoped to `unsupported_inferred`, and the refusal message says which kind
the user is looking at — a user reading "this was determined by reading the install source" knows
they might know better, which is exactly the situation `--force` is for.

`compat.set_force()` is process-wide state rather than a threaded parameter, matching
`verbose.py`; the install path reaches `GenericTool.install()` from four different callers and
threading a flag through all of them is what that module already decided against.

## SD-14. Rejected: shelling out to `pkg` for installed-state

Termux's `pkg` has no query verb of its own — `pkg show` maps to `apt show`, which reports the
repository's view, not the installed one. The installed-state probe therefore uses `dpkg -s`
(or `pacman -Q`) directly, same as Debian. This is the one place the abstraction reaches past the
wrapper, and it is because the wrapper has nothing to offer there.
