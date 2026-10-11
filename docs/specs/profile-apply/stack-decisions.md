# Stack decisions: `devstuff profile apply`

**Date:** 2026-10-10

---

## SD-1 — A pinned install is one `update(version=…)` call; the installers don't change

**Chosen:** to install a missing tool at a pinned version, call `tool.update(version=pin)` directly.
It runs the same command `install()` runs with a version in it, and was measured to work on a tool
that isn't installed (spec F-3).

**Rejected:** install-then-update (the shape `profile` §6 predicted). It installs the latest and
then replaces it: two network installs, a throw-away intermediate state that a failure between the
two steps would leave behind, and for a pin older than latest a downgrade of something that was
never wanted at that version.

**Rejected:** teaching `install()` an optional `version` for `npm`/`uvx`/`apt`. It would touch every
installer and its callers to add a parameter whose only consumer already exists under another name.
The name is the one awkward part — "update" on a missing tool reads oddly — so the planner labels
the step `install pinned` and a comment at the call explains why it is not `install`.

**Caveat recorded, not hidden:** `update()` for `uvx` prints a "Pinning X… stays there until the next
update" warning meant for someone pinning by hand. During `apply` it is noise, and is accepted rather
than adding a quiet mode to `update` for one caller.

## SD-2 — Plan from `diff`'s classifier, not a second comparison

**Chosen:** `apply` runs `profile.compare` over `machine_facts` and turns each row into an action.

**Rejected:** a bespoke "what needs installing" pass. There would then be two definitions of
"missing" and "drift", and the day they disagree `diff` would say a machine matches while `apply`
changes it, or the reverse. The whole point of the seven-state classifier is that "could not
compare" is never "fine"; `apply` inherits that for free by not re-deciding it.

It also gives the final report (FR-15) for nothing: the same function, run again.

## SD-3 — The result is a re-measurement, not the sum of step results

**Chosen:** after the last step, drop every cached probe, run `compare` again, and report and exit
from *that* (FR-15, FR-17).

**Rejected:** exit 0 when every step's call returned. An installer returning without raising is
what `devstuff install` already treats as success, and the repository's own history is a catalogue
of that being wrong — `pip_name: ansible` "succeeds" and installs one unusable binary. A command
whose job is to make a machine match a file should be judged by whether it does, using the same
test a user would run next (`profile diff`). It is also what makes the idempotence requirement
(NFR-4) a single assertion.

**Cost accepted:** a second full probe pass (the local reads took 0.7–1.0 s over 13 installed tools
when measured for `profile`, spec F-3 there), and a pinned step
on a machine where `uv` is not on the process's PATH reads "installed, version unverified" rather
than `ok` (F-5). The honest answer is "I could not confirm", and the exit status says so.

## SD-4 — Continue past a failure; skip dependents with a reason

**Chosen:** a failed step marks everything that requires it as skipped, with the reason, and the run
carries on (FR-9).

**Rejected:** stop at the first failure. It is the simplest to implement and the worst outcome
for a large profile: the machine is half built and the user has no list of what is left.

**Rejected:** try dependents anyway and let them fail. `devstuff install` already refuses to
install a tool whose `requires` are missing; attempting it inside `apply` produces a second,
confusing error for every dependent of one real failure.

## SD-5 — Serial, not parallel

**Chosen:** one step at a time, in plan order.

**Rejected:** installing independent branches concurrently. `snapshot`/`diff`/`outdated` use a pool
because they are read-only lookups that wait on subprocesses. Installers are not independent: apt
takes a global lock, `nvm`-backed npm installs share one prefix, scripts write to `~/.local/bin`,
and any of them may stop to ask for a sudo password. Parallel output interleaves unreadably, a
password prompt in a worker is unanswerable, and a failure's dependents would race the failure. The
saving is real but small next to the cost of an installer that is hard to reason about.

## SD-6 — No removal, no pruning

**Chosen:** `apply` only ever adds or moves a tool to a version the profile names.

**Rejected:** `--prune` in v1 (OQ-7). Everything else here can be corrected by running `apply` or
`install` again; deleting software cannot. A profile that was hand-trimmed, or a partial profile
meant for a bundle (`@backend`), would otherwise delete the rest of the machine. `diff` already
lists extras, so the information is available without the destructive verb.

## SD-7 — `apt` pins are attempted, not pre-validated, and never forced

**Chosen:** run `apt-get install pkg=ver` and report apt's outcome (FR-8).

**Rejected:** adding `--allow-downgrades`. Measured: it changes nothing for the cases that matter,
because they fail on version-locked dependencies, not on the downgrade rule (F-2).

**Rejected:** pre-checking the pin against `apt-cache madison`. A version being *listed* doesn't
mean it's *installable* — `curl`'s older version is listed and cannot be installed — so a pre-check
would give false confidence and a second source of truth that disagrees with apt.

## SD-8 — The unattended refusal is a hard stop, not a warning

**Chosen:** no TTY and no `--yes` is exit 2 with a message (FR-12).

**Rejected:** treating a non-TTY as implicit consent, which is how a script run from cron changes a
machine without anyone having chosen it; and prompting anyway, which blocks forever in CI. A decision that
changes the machine is one the user makes, not one a missing terminal makes for them.

## SD-9 — Bundles are shipped profile files, resolved by `@name`

**Chosen:** `@backend` loads `bundles/backend.yaml` from the package through the ordinary loader
(FR-20), so there is exactly one file format and one validator.

**Rejected:** bundles as a new catalog concept (a `bundles:` section in `tools.yaml`). It would add a
second schema to validate and document for something a profile already expresses, and it would make
a bundle impossible to copy out and edit — which is the first thing anyone does with one.

**Rejected:** bundles that fetch from a URL. A profile can't carry code (`profile` SD-2), but the
network is a new trust and availability dependency for a command that otherwise works offline for
everything but the installs themselves.

## SD-10 — A separate step runner; `install_cmd` is left alone

**Chosen:** `apply_exec.perform(tool, step)` is new code that calls `tool.install()` or
`tool.update(version=…)`, catches failure into a result, and forgets cached probes. `install_cmd` is
not modified.

**Rejected (the original plan, A2 as first written):** factor a shared non-interactive core out of
`install_cmd._install_one` and make `devstuff install` call it. Measured when A2 started: the part
the two would actually share is one `try`/`except` around a single call. Against that, `install_cmd`
has essentially no tests to refactor against (one starship test touches it), and it is the file with
the largest uncommitted rewrite in the maintainer's working tree — a refactor there buys a
near-empty abstraction at the price of a merge conflict and an unprotected behaviour change.

**The duplication that remains is the wizard offer and the section header, which `apply` must not
have anyway.** If the two ever need to share more, that is a change to make with `install`'s own
tests in place first.
