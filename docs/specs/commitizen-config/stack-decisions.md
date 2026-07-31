# Stack Decisions: `devstuff configure commitizen`

**Date:** 2026-07-31
**Context:** The second configurator, built inside the structure `docs/specs/starship-config/`
established. SD-1 and SD-2 there (configurators are Python modules in a registry, and the
registry is keyed in Python rather than declared in `tools.yaml`) apply here unchanged and are
not restated. Everything below is specific to commitizen.

---

## SD-1 — The wizard configures rules, not a settings sheet

**Decision: the primary object the wizard manipulates is a list of commit types, each carrying a
bump level, a changelog heading and a hotkey. Every regex commitizen needs is derived from it.**

commitizen's `cz_customize` block is nine settings that only work when they agree with each
other: `bump_pattern` and `bump_map` (whose keys are matched against `bump_pattern`'s **group
1**, not the message), `changelog_pattern` and `commit_parser` (whose named groups the changelog
generator reads), `schema_pattern` and `message_template` (which must describe the same
message), `change_type_map` and `change_type_order` (which must name the same sections), and
`questions` (whose `change_type` choices must be the same set of types as all of the above).

- **Rejected — one prompt per commitizen setting.** It is a faithful mapping of the config file
  and a useless wizard: the user would be typing regexes into questionary, which is a worse
  editor than the editor they already have. Nothing would stop them writing a `bump_map` key
  that no `bump_pattern` group can ever produce.
- **Rejected — a fixed set of presets** ("conventional", "conventional plus deps", …). Covers the
  first two teams and nobody else, and the whole reason to reach for `cz_customize` is that the
  shipped set did not fit.

**Consequence:** `model.py` holds the type table and `render.py` derives nine settings from it.
Adding `deps` is one `ChangeType` record; it reaches `bump_pattern`, `bump_map`, `schema_pattern`,
`change_type_map`, `change_type_order`, `commit_parser` and `questions` with no other edit —
which is what the "added types reach every generated rule" test asserts.

## SD-2 — Two conventions, and the custom one starts as a copy of the built-in one

**Decision: `cz_conventional_commits` and `cz_customize`, with the customizable branch's defaults
reproducing commitizen's own rules exactly.**

The switch from Conventional Commits to `cz_customize` is where most of commitizen's difficulty
lives, and a user makes it for one small reason — one extra type, or `docs:` releasing when it
should not. If the custom branch started from a blank slate, that one small reason would cost
them the whole convention.

**Consequence:** the built-in table's bump levels and changelog membership are `BUMP_MAP` and
`ConventionalCommitsCz`'s `change_type_map`, read out of the installed package and pinned by
`test_defaults_reproduce_commitizens_own_rules`. Switching conventions on the review menu changes
`name` and whether a `customize` block is emitted, and nothing else the user has to redo.

Under `cz_conventional_commits`, the model deliberately *ignores* the user's type selection and
bump overrides: commitizen's rules are what will run, so a preview reflecting the overrides would
describe a config that cannot exist.

## SD-3 — TOML emitted as text, read back with `tomllib`

**Decision: write the file by string-building; verify by parsing.**

Same reasoning as the starship configurator's SD-4 — the header comments and the "order matters"
note above `bump_map` are part of the deliverable, and no stdlib writer emits comments. But
commitizen adds a wrinkle starship did not have: nine of the emitted values are regexes.

- **Rejected — `tomlkit`** (a comment-preserving TOML writer, and already a commitizen
  dependency). It is not a devstuff dependency, and devstuff is a globally installed CLI where
  every dependency is paid for by users who will never run this wizard (the agent's
  "no new runtime dependencies" rule).

**Consequence:** values go out as TOML **literal** strings (`'…'`), which process no escapes, so
`^((BREAKING[\-\ ]CHANGE|feat)(\(.+\))?!?):` appears in the file exactly as written instead of
with every backslash doubled. `message_template` and `schema` are the exceptions: they carry real
newlines, so they are basic strings with `\n` escapes. A value containing a quote falls back to a
basic string automatically — a user typing `Bill's changes` as a changelog heading must not
produce a file that will not parse.

## SD-4 — The rules are checked against the real binary, not asserted

**Decision: a throwaway git repo, tagged at 1.4.2, with one commit per bump level replayed
through `cz bump --dry-run`.**

The wizard shows a table saying `feat: … → MINOR → 1.5.0`. That table is derived from the same
`bump_map` the file gets, so it is *internally* consistent by construction — and internal
consistency is exactly what is worthless here. The failure mode is the generated map not meaning
what we think it means to commitizen: group 1 of `bump_pattern` not being the string `bump_map`
is matched against, the map's key order not mattering the way we assumed, `!` not reaching the
breaking rule.

Reading commitizen's `find_increment` settled those questions; running it proves they stay
settled. Every one of the design details in the emitter's comments — first-match-wins ordering,
group 1 as the matched string, `bump` needing to be in `schema_pattern` — came out of this loop.

- **Rejected — importing commitizen and calling `find_increment` directly.** It would make
  commitizen a devstuff dependency to check a config for a tool the user may not have installed,
  and it would test commitizen's internals rather than the `cz` binary the user will actually
  run.
- **Rejected — running it on every redraw** (what the starship wizard does). One `starship
  prompt` is milliseconds; this is ~8 `cz` invocations and a git repo, about three seconds. It is
  an explicit menu action plus one automatic run at save time.

**Consequence:** `validate.py`. It replays one commit per *distinct* bump level rather than per
type — the type→level mapping is our own table, and the levels are what commitizen is being
asked about. The preview config swaps `version_provider` to `commitizen` with a fixed version so
the sandbox does not need the user's project; everything actually under test is emitted verbatim.

## SD-5 — A disagreement warns, it does not veto

**Decision: the pre-save check reports failures and asks; the user can always save.**

The check is a cross-reference between two things that can both be right — commitizen's
behaviour and the wizard's model of it. When they diverge, the most likely cause is that
commitizen changed and devstuff has not caught up yet. A check that can refuse to write the
user's file turns that into a hard block on a tool the user owns.

This is the same distinction the agent spec draws between `assess()` (advisory) and
`check_command` (enforced): say which one a mechanism is, in the comments, so a later reader does
not mistake a warning for a control.

## SD-6 — No round-trip of an existing config

**Decision: the wizard always starts from detected defaults; existing files are backed up, not
parsed into wizard state.**

Reading a `[tool.commitizen]` block back into a `CommitizenConfig` is easy for the settings and
impossible for the rules: nothing in a `bump_map` says which of its keys the user typed by hand,
a `schema_pattern` cannot be decomposed into a type list without assuming this wizard wrote it,
and a hand-written `commit_parser` may encode something the model has no field for.

- **Rejected — partial reconstruction** (settings yes, rules no). It produces a wizard that
  silently discards the interesting half of what it just read, which is worse than one that is
  honest about starting fresh.

**Consequence:** `detect.read_existing()` exists only to tell the user what they are about to
replace. The timestamped backup is the safety net, and the pyproject splice means the rest of
the file survives regardless.

## SD-7 — TOML only, but two destinations

**Decision: emit TOML; offer `pyproject.toml` (spliced) or `.cz.toml` (whole file).**

commitizen reads six filenames plus `pyproject.toml`, in JSON, YAML and TOML. Supporting all
three formats would mean three emitters and three merge strategies for one behaviour.

TOML is the form every commitizen example is written in, the only one `pyproject.toml` can hold,
and the only one where a literal string keeps a regex readable — the JSON version of
`bump_pattern` has every backslash doubled. A JS project that would rather use `.cz.yaml` can
still have the wizard's `.cz.toml`; commitizen does not care which it reads.

**The splice, and why it fails closed.** Writing into `pyproject.toml` cannot be "render the
whole file": that file belongs to the project, and the wizard has no business reformatting
someone's `[tool.ruff]` block. So it is a line-based replacement of every `[tool.commitizen…]`
table — which is a text operation with a known failure mode (a table header inside a multi-line
string). Rather than reasoning about whether that can happen, the result is parsed back and
compared to what we meant to write; a mismatch means the file is left untouched and a `.cz.toml`
is written instead.

- **Rejected — always writing `.cz.toml`.** In a Python project that already has a
  `pyproject.toml`, adding a second config file is the thing that causes commitizen's "Multiple
  config files detected" warning, and `.cz.toml` wins the search order — so it would silently
  shadow settings the user may not remember having.

## SD-8 — `cz_jira` and third-party plugins are out

**Decision: the wizard offers Conventional Commits and `cz_customize`.**

`cz_jira` is a built-in plugin with fixed rules and no configurable bump behaviour, so it has
nothing for this wizard to configure — offering it would put a dead end in the first question.
Third-party plugins (`cz_gitmoji`, `cz_legacy`, …) are separate pip installs whose settings this
wizard cannot know. A user who wants either can set `name = "…"` in the generated file by hand;
everything under `[tool.commitizen]` still applies.

## SD-9 — The commit-msg hook is offered, not installed

**Decision: after saving, offer to write `.git/hooks/commit-msg`; never replace an existing one.**

A convention nobody enforces is a convention that decays, and `cz check --commit-msg-file` is the
documented one-liner for it. But a commit hook changes what `git commit` does for everyone
working in that clone, which is not something a config wizard should do silently.

The "never replace" rule is not politeness: an existing `commit-msg` hook may be doing something
the user cares about, and merging two shell scripts is not a yes/no question. If one is there,
the offer is not made at all.

- **Rejected — writing a `.pre-commit-config.yaml` entry instead.** It means owning a second
  file's merge semantics (and pre-commit may not be installed), for the same outcome.
