# uv as the install backbone — stack decisions

Companion to `specifications.md`. Each entry records the choice **and** what was rejected.

## SD-1. Extend the existing `uvx` type rather than adding a `uv-tool` type

**Chosen:** add three optional fields to the existing `uvx`/`pip` types.

**Rejected: a new `uv-tool` install type.** Per CLAUDE.md, a new type costs edits in five places
and, more importantly, would leave two types meaning almost the same thing. `uvx` already dispatches
to `uv tool install`; the fields are the missing expressiveness, not a missing mechanism. The
existing `pip` alias keeps working because it shares every strategy function with `uvx`.

## SD-2. `--with-executables-from` rather than installing `ansible-core` directly

**Chosen:** `pip_name: ansible` + `uv_executables_from: [ansible-core]`.

**Rejected: `pip_name: ansible-core`.** It would produce the eleven binaries with no extra field
and no engine change — genuinely tempting. But `ansible-core` ships *only* the core engine; the
several hundred bundled collections (`community.general`, `amazon.aws`, `ansible.posix` …) live in
the `ansible` distribution. Choosing it would silently narrow what the installed ansible can do,
and the user would discover it as "module not found" on their first playbook. The whole point of
this spec is to stop shipping a differently-shaped ansible than the one people expect.

**Rejected: `pip_name: ansible` alone.** Measured to install one unusable executable (F-1). This is
the bug being fixed.

## SD-3. Correctness over uv coverage on borderline tools

**Chosen:** convert only where the uv artefact is the same tool or better. Two conversions.

**Rejected: maximise uv coverage.** Offered and declined. The measurements (F-6) show why it would
have been actively harmful: PyPI `bat`, `lazygit` and `git-lfs` are three *unrelated* projects that
happen to share a name, PyPI `yq` is a different YAML processor with different syntax, and PyPI
`awscli` is a major version behind what the catalog installs. "Everything is a uv tool" would have
replaced four working tools with four wrong ones and downgraded a fifth.

The honest consequence is that the conversion list is short. That is the correct answer, not a
disappointing one — the leverage in this work is the engine and the ansible fix, not the count.

## SD-4. No bookkeeping of install flags across upgrades

**Chosen:** rely on uv's own receipt.

**Rejected: recording `uv_with`/`uv_python` in devstuff state and re-applying them on update.** This
was the assumed design until `uv-receipt.toml` was inspected (F-3): uv already persists
`requirements`, `python` and the entry-point→source map, and `uv tool upgrade` honours all of them.
Duplicating that in devstuff would add a second source of truth that can drift from uv's.

Note the ordering here — the receipt was *measured*, not assumed. A plausible-sounding memory would
have said flags are lost on upgrade, and the wrong feature would have been built.

## SD-5. `uv tool install --force` for *every* uvx update, not `uv tool upgrade`

**Chosen:** both update paths go through `uv tool install --force` — `<pkg>==<ver>` when pinned,
`<pkg>@latest` otherwise.

**Rejected: keeping `uv tool upgrade <pkg>==<ver>`.** Measured to fail outright (F-5) — `uv tool
upgrade` parses its argument as a tool name, so the `==` is part of the name and nothing matches.
The current code has never worked for this path.

**Rejected (revised 2026-08-12): keeping `uv tool upgrade` for the *unpinned* path.** This was the
original decision and it was wrong. `uv tool upgrade` is also a no-op on an already-pinned tool
(F-10), so pairing it with a pinned-install path created a one-way door: pin once and devstuff
could never move the tool again. `install <pkg>@latest` re-resolves and clears the pin in a single
call, which collapses the whole problem.

The cost is that `--force` writes a fresh receipt, so the `uv_*` flags must be re-passed on update
(FR-10a). That turns out to be a feature — the flags are re-derived from the catalog, so adding a
`uv_with` to a tool takes effect on the next update instead of requiring a manual reinstall.
This narrows SD-4 rather than contradicting it: devstuff still stores no *copy* of uv's state; the
catalog remains the single source of truth for what a tool's environment should contain.

**Rejected: `uv tool install <pkg>@<ver>`.** uv accepts the `@` form, but `==` is what the rest of
the catalog schema and the `devstuff update --version` UI already speak. No reason to introduce a
second spelling.

## SD-6. A pinned install warns rather than being refused

**Chosen:** warn that the pin holds until the next unpinned update, then do it.

**Rejected: refusing exact pins,** and **rejected: silently reinstalling unpinned on the next
update.** Pinning is a legitimate thing to want; silently undoing it would be worse than the
footgun. The project's precedent (the commitizen configurator's validator) is that a disagreement
warns and never vetoes.

## SD-7. The ansible helper is a `script` function, not `shell-eval`

**Chosen:** `type: script` — it reports which ansible is in effect and exits.

**Rejected: `shell-eval` with `register: eval`,** which would let it activate a project venv in the
calling shell. That is what `uv run` already does, better, and a devstuff-owned venv activation
competing with uv's is exactly the kind of fight this spec is trying to end. The helper's job is to
answer a question, not to change the environment.

**Rejected: making it a wrapper that execs `ansible-playbook`.** Users would have to remember to
call devstuff instead of ansible, forever, in every repo. A diagnostic they run once when confused
is worth more than a permanent indirection layer.

## SD-8. `python` is a `bash`-type tool, not a new type

**Chosen:** a `bash` entry whose script calls `uv python install --default`, `requires: [uv]`.

**Rejected: a `uv-python` install type.** One tool does not justify a type; every other version
manager in the catalog (`nvm`, `ruby`/rbenv, `go`) is likewise a `bash` entry. If uv-managed Python
ever needs per-version catalog entries, revisit.

The `check_cmd` is the subtle part (FR-16): it must detect a *uv-managed* interpreter, because
`command -v python3` succeeds on every Linux host and would make the tool report itself installed
before it ever was. Same class of trap as the `nerd-font` and `go` check commands, which is where
the pattern is borrowed from.

## SD-9. Extras stay in `pip_name`; no `pip_extras` field

**Chosen:** document that `pip_name: "pkg[extra]"` works.

**Rejected: a `pip_extras: list[str]` field.** The install path never goes through a shell — argv
is passed as a list to `subprocess` — so brackets need no quoting and the plain string is already
correct. A field would be a second way to spell the same thing.
