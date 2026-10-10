# Stack decisions: `devstuff outdated`

**Date:** 2026-10-10

---

## SD-1 — A new command, not a flag on `update`

**Chosen:** `devstuff outdated` as its own read-only command.

**Rejected:** `devstuff update --check` / `--dry-run`. It is the smaller diff and it keeps one
command per concept. But `update` is defined by being the one that mutates: its confirmation
flow, `--version`, plan previews and failure exit codes all describe a write. A `--check` flag
would have to switch all of that off, leaving a command whose help text is mostly "except when
`--check`". It would also be undiscoverable — nobody looks under `update` for a report.

**Rejected:** `devstuff list --outdated`. `list` answers "what exists and is it installed",
and it already probes for installed state and version. Adding a network probe per tool to
a command people run for a quick glance would make the common case slow.

## SD-2 — Five states, with `unsupported` decided by type

**Chosen:** `outdated` / `current` / `unknown` / `unsupported` / `not-installed`, where
`unsupported` comes from `install_type not in _UPDATE_CHECKERS`.

**Rejected:** three states from `UpdateStatus.available` alone (`True`/`False`/`None`). That is
what the picker does today, and it is why the picker's label for 23 tools is a vague hint.
`None` conflates *this type has no checker* with *the checker ran and failed*; the user's
remedy differs, and a report that cannot tell them apart will be trusted less when it matters.

**Rejected:** adding a `supported: bool` to `UpdateStatus`. The command layer can already
answer it from the checker table, so a new field would be a second source of truth for a fact
that has one.

## SD-3 — `unknown` and `unsupported` are never green

**Chosen:** distinct glyph, colour and wording for both, and a test per state.

**Why this is a decision rather than a styling detail:** this is the same failure the project
has already met. The starship font gate is allowed to answer "don't know", where `None` means
stay silent, because inverting "cannot tell" into "no" nags every user without fontconfig. Here
the inversion is worse — "cannot tell" shown as "up to date" gives false confidence about
two thirds of the catalog, and `profile diff` (M2) will inherit whatever convention this
command sets.

## SD-4 — Share the collector by moving it, typed against `GenericTool`

**Chosen:** move `_collect_update_candidates` out of `commands/update_cmd.py` into a module
both commands import, keeping its signature and its "no UI code" property.

**Rejected:** `outdated` importing the private function from `update_cmd`. It works today and
couples two commands through a leading underscore.

**Rejected:** duplicating the probe loop. That is the failure mode in CLAUDE.md for
`catalog.py`/`registry.py` vs. the functions subsystem — duplication accepted *deliberately*,
where the schemas diverge. Here they do not; both commands need the identical answer.

**Rejected for now:** adding `check_for_update` to the `Tool` ABC. The ABC is the contract for
any tool implementation, and extending it for one consumer changes it for all. The collector
filters to objects that have the method; if a second implementation ever appears, promote it
then. This also lets the `# type: ignore[attr-defined]` in `update_cmd` disappear.

## SD-5 — Collapse `unsupported` by default

**Chosen:** one footer line, with `--all` to expand.

**Rejected:** a row each. On the bundled catalog that is 23 identical "can't check" rows above
or below a handful of real answers. The collapsed line still states the count and names the
tools, so the blindness stays visible (SD-3) without dominating the table.

## SD-6 — Exit 0 regardless of findings

**Chosen:** exit status reports whether the *lookup* ran. This is the rule `run_cmd` already
imposes (CLAUDE.md: "a `script` function cannot signal a *result* through its exit code"), applied
to a command whose natural result is exactly the thing people want to gate on.

**Deferred, not rejected (OQ-3, resolved 2026-10-10):** `--exit-code`. It is a legitimate CI feature, and being an
opt-in flag it does not break the default. It waits for a real use rather than being built on
the guess that someone will want it.

## SD-7 — `--json` as the only integration surface

**Chosen:** a stable array of six-field objects on stdout, nothing else.

**Rejected:** a shell-prompt segment, a cron-friendly notifier, a `~/.cache` file refreshed in
the background. Each is a feature with its own questions (staleness, locking, where it writes),
and each is one `jq` pipeline away for anyone who wants it. `profile diff` needs the same data
in-process, not via a file.

## SD-8 — No cross-run cache

**Chosen:** every run probes live.

**Rejected:** caching latest-version lookups on disk with a TTL. A stale "up to date" is the
exact error SD-3 exists to prevent, and a cache adds invalidation to a command that is currently
stateless. The measured cost (2.6 s for 13 tools) does not justify it. If latency becomes the
complaint, remove the redundant probes first (FR-19) — that is a 2× win with no staleness.
