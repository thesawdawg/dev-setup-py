# Stack decisions: `devstuff profile`

**Date:** 2026-10-10

---

## SD-1 — A new file format, not an extension of the catalog

**Chosen:** a profile is its own small YAML file with the catalog's *shape* (`version`, `tools`) but
its own meaning: "which of the catalog's tools should be here".

**Rejected:** an `installed: true` / `pinned_version:` field on catalog entries, so a catalog doubles
as a profile. A catalog describes what a tool *is* and how to install it; a profile describes what
a *machine has*. They have different lifetimes (a catalog changes when devstuff or a tool does; a
profile changes when the machine does) and a profile has to load against *different* catalogs —
the whole point is to carry it to a machine whose catalog lacks some of its keys (FR-6). Fusing
them would also make every catalog validation rule apply to a file that deliberately references
things the catalog may not define.

**Rejected:** extending `devstuff catalog export` to write installed state. `export` serialises
*definitions the user added*; installed state is a property of the machine, not of the catalog.

## SD-2 — Keys only: a profile never carries tool definitions or scripts

**Chosen:** an entry names a catalog key and optionally a version. Nothing else.

**Why this is a security decision as much as a design one:** catalog entries contain install and
remove scripts that run as the user, sometimes with `sudo`. If a profile could embed a definition,
then `profile apply` of a file from a gist or a colleague would be arbitrary code execution that
bypasses the catalog's validation and the user's own review of what is in it. Keeping profiles to
keys means the worst a malicious profile can do is ask for tools the user's own catalog already
defines. A custom tool that exists on one machine only is reported as `unknown-key`, and moved by
`devstuff catalog export` / `import`, which is already the reviewed path for definitions.

**Rejected:** embedding custom definitions "for convenience" with a confirmation prompt. A prompt
is an attention filter, not a control (the same stance the agent's sandbox takes), and `apply`
exists to be run unattended.

## SD-3 — Versions come from per-type local readers, never from `get_version()` text

**Chosen:** `GenericTool.installed_version()` dispatching on install type to a local-only read.

**Rejected:** extracting the first version-looking token from `get_version()` with a regex
(`\d+(\.\d+)+…`). Measured on the 13 installed tools: **12 correct, 0 wrong, 1 nothing**
(`nerd-font` prints no version) — so on today's catalog it would work, and that is the trap. The
failure is not on a tool we have seen but on the next one: `Python 3.12.1 :: devtool 0.4.0`,
`libfoo 2.38 (devtool 1.9.0)` and `built 2026.10.10 release 1.9.0` each yield the *wrong* token
(`3.12.1`, `2.38`, `2026.10.10`) with no error, and the result is a wrong pin written silently that
`apply` would later try to install. The output is free-form by nature; a reader that fails loudly
(`""` → FR-12's warning) is worth more than one that is right until it isn't.

**Rejected:** calling `check_for_update()` and taking `.current`. It is clean, but it is fused with
a network lookup of the latest, so `snapshot` would need the network and take as long as the slowest
registry timeout (~11 s offline, measured). A snapshot of what is installed has no business asking
the internet anything.

## SD-4 — Only pinnable types are ever given a version

**Chosen:** `--versions` records a version for `npm`, `pip`, `uvx` and single-package `apt` only.

**Rejected:** recording a version for every tool "for information". `git`/`script`/`bash` tools
update themselves or are re-fetched, so a recorded version turns into reported drift the next time
they do, with nothing `apply` could do about it. A diff that cries wolf on two thirds of the
catalog (the same share `outdated` found to be `bash`) teaches people to ignore it.

## SD-5 — Seven states, and the three "couldn't tell" ones are never `ok`

**Chosen:** `ok`, `missing`, `drift`, `unverifiable`, `unpinnable`, `unknown-key`, `extra`.

`unverifiable` (pinned, installed, version unreadable), `unpinnable` (the type cannot honour a
pin) and `unknown-key` (the catalog does not know it) are three different reasons the comparison
could not be made, with three different remedies. Collapsing them into one "unknown" loses the
remedy; folding any of them into `ok` is the failure `outdated` was built around (its SD-2,
SD-3, and the F-7 bug where a failed probe read as current). They are separate states, rendered
separately, and counted separately so the totals cannot hide them.

## SD-6 — Strict loading: reject coerced versions and duplicate keys

**Chosen:** a non-string `version` is an error; a duplicate key is an error.

**Rejected:** coercing with `str()`. By the time Python sees the value, `1.10` is already the float
`1.1` — the trailing zero is gone from the *data*, not from the printing — so `str()` yields a
version that does not exist and that nothing on the machine can match. The only safe move is to
refuse and say "quote it". The same reasoning is in the pre-commit configurator's quoting rule.

**Rejected:** warning instead of failing. A profile is read by `diff` and later `apply`; a warning
scrolls past, and a wrong version or a lost entry then acts on the machine.

Duplicate keys need a custom loader because PyYAML does not detect them. It is a few lines, and
the alternative is a hand-edited file silently losing whichever entry came first.

## SD-7 — Deterministic output, no timestamp, no hostname

**Chosen:** sorted keys, a fixed header comment, nothing machine-identifying.

**Rejected:** a `# generated on <host> at <time>` header. It makes every snapshot differ from the
last, so a profile committed to git shows a changed line each run and real changes drown in it; and
it puts a hostname and a time in a file whose purpose is to be shared.

## SD-8 — Errors exit 2; exit 1 means "differs"

**Chosen:** with `--exit-code`, `1` means the machine differs from the profile; an unreadable or
invalid profile is `2`.

**Why not the project's usual "errors are 1":** `outdated` can use 1 for errors because 1 never
means anything else there. Here a CI gate must tell "the machine drifted" from "the file was
malformed", and both being 1 would turn a typo in the profile into a drift alert, or the reverse.
`git diff --exit-code` draws the same line.

## SD-9 — `diff` shows differences by default

**Chosen:** `ok` rows are summarised, not listed; `--all` lists them. `extra` rows collapse to one
footer line.

**Rejected:** listing everything. The question `diff` answers is "what is wrong", and on a machine
that mostly matches, 30 `ok` rows hide the three that do not. `outdated` made the same call for
`unsupported` rows, for the same reason.

## SD-10 — `supports_pin` is an explicit predicate, tied to behaviour by a test

**Chosen:** a named set of pinnable types plus a test that calls `update(version=…)` for every
install type and asserts it agrees.

**Rejected:** deriving it by calling `update` and catching "not supported". That would make a read
(`diff`) attempt a mutation to find out what it can do. **Rejected:** a bare hard-coded set with no
test — it is correct today and drifts the first time someone adds a type or teaches `git` to pin.

## SD-11 — A `profile` group, not top-level `snapshot` / `diff`

**Chosen:** `devstuff profile snapshot|diff`, with `apply` joining as a third subcommand in M3.

**Rejected:** `devstuff snapshot` and `devstuff diff`. `diff` alone is far too general a name to
spend at the top level, and the three verbs share a loader, a file format and a state vocabulary —
they are one feature. `devstuff outdated` is a top-level verb because it stands alone; these do not.
