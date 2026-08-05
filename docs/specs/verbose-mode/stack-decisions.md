# Stack decisions: verbose mode

**Date:** 2026-08-04

---

## SD-1 — A module-level level, not Python's `logging`

**Chosen:** a tiny `verbose.py` holding an int and five printers on a Rich stderr console.

**Rejected:** `logging` with a `StreamHandler` and `setLevel`. It is the obvious answer and it
buys nothing here: there are no log files, no per-module filtering, no third-party libraries
whose loggers we want to reconfigure, and no format that survives `logging`'s own formatter
better than an f-string. What it *would* add is a global side effect on `logging.root` from a
CLI that other tools may import, plus a second styling system alongside Rich.

**Rejected:** threading a `verbose: int` parameter through `install()` / `remove()` /
`is_installed()`. The subprocess calls that need it are four layers below the command handler,
across `GenericTool`, `function_runner` and the strategy dicts — every one of those signatures
would grow a parameter that only two lines in each ever read. `Tool` is an ABC with third-party
implementations in principle; changing its contract for logging is the wrong trade.

## SD-2 — Set by a Click callback with `expose_value=False`

The option is applied to every command by `cli._add_verbose_option`, walking the group tree.

**Rejected:** decorating each command with `@verbose.option`. That is what the first draft did,
and it immediately produced the bug it was always going to: `devstuff list -v` errored with
"No such option" because `list` hadn't been decorated. Users type `-v` on whatever command is
in front of them; a flag that works on four commands out of fourteen is worse than none.

**Rejected:** a group-level option only. `devstuff -v install x` is the unusual spelling; every
other CLI accepts `install -v` and users will type it first.

`expose_value=False` keeps the level out of command signatures, which is the point of SD-1.

## SD-3 — Everything to stderr, no exceptions

This is a correctness constraint, not a style choice. `devstuff run <key>` for a `register: eval`
function prints shell code to stdout precisely so `eval "$(devstuff run key)"` works. A verbose
line on that channel is not noise — it is **code the user's shell will execute**.

The logger therefore has no stdout path at all. A per-call-site "don't log in eval mode" check
would work today and be forgotten the first time someone adds a log line.

## SD-4 — `bash -x` for extra-verbose, not a custom tracer

`-vv` runs script bodies under `bash -x`. Bash already prints each expanded command with its
variables substituted, which is exactly what "which line failed" needs and is not reproducible
by logging the script text alone.

**Not applied to `register: eval` scripts.** `set -x` inside evaluated code persists in the
caller's interactive shell after the function returns — the trace would follow them around until
they typed `set +x`. Those scripts are logged instead.

## SD-5 — Two subprocess helpers, split by "does this change anything"

`generic.py` now has `_run` (state-changing: streams when verbose, raises `RuntimeError`) and
`_probe` (captures, never raises, logs at `-vv`). Everything that shells out goes through one of
them, which is what makes the flag's coverage complete rather than a list of places someone
remembered.

The split is by **consequence**, not by whether output is captured: a probe's output is captured
because the caller parses it, so verbosity can only add logging there. The `log_at` parameter
exists for the two commands that are best-effort *actions* (`apt-get update`, a git tool's
`git_remove_cmd`) — captured and non-fatal like a probe, but work the user should see at `-v`.

**Rejected:** logging probes at `-v`. Measured: `devstuff -vv list` emits ~60 probe lines before
any output. At `-v` that would hide the one line the user asked for.

## SD-6 — The spinner is swapped out, not left running

`verbose.step` is a spinner when quiet and a printed line when verbose. Rich's `console.status`
repaints its own line on a timer; with a subprocess writing to the same terminal, the two
interleave and the result is unreadable. This also fixed the pre-existing gap where npm/uvx/git
installs used a spinner *and* captured output, so the old `-v` flag did nothing for them.

## SD-7 — `DEVSTUFF_VERBOSE` can only be raised by `-v`, never lowered

`set_level(max(level(), value))` in the callback. The env var is for a context that can't pass
flags (a wrapper script, CI); an explicit `-v` on top of `DEVSTUFF_VERBOSE=2` shouldn't quieten
anything down, and `-v` meaning "less verbose" would be indefensible.

---

## Open questions

| Question | Status |
|----------|--------|
| Should `-vv` redact function parameter values that look like secrets? | Open. Deliberately not done in v1 (see spec §3) — a partial redactor implies a guarantee devstuff can't make. |
| Should the agent's tool calls honour the level? | Open. The agent prints its own transcript; a second channel may just duplicate it. |
