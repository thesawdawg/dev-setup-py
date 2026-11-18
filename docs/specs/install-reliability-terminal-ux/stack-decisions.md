# Install reliability & terminal UX — stack decisions

Initial decisions with implementation revisions noted below. Rejected alternatives
carry their reasons — they are the part that gets re-litigated later.

## Accepted

- **D-1 — Keep Click + Rich + questionary.** The current stack already renders every
  surface this work touches; the failures in the evidence table are lifecycle and
  policy gaps, not widget gaps. A new base dependency has to prove itself at the TUI
  gate (D-8), not by default.
- **D-2 — One shared lifecycle, renderer-agnostic.** A single supervised
  child-runner serves live, verbose, plain and non-TTY/JSON modes; render mode
  selects *presentation*, never timeout/EOF policy (R1). This is what finally closes
  the F-8 class of bugs instead of patching each mode separately (E-1).
- **D-3 — Stdlib Linux process supervision + narrow callbacks (revised 2026-11-17).**
  `subprocess` + `select`/process-group primitives deliver output and a
  drain/activity notification. The phase UI keeps its own phase boundaries and
  refresh cadence; it does not depend on a broad typed event enum for phase start,
  idle, exit, cancellation or result. The result comes from the command's exit
  and explicit postcondition probes — **never from parsing stdout**.
- **D-4 — `_run` stays the adapter, `_probe` keeps its purpose.** Existing call
  sites keep their signatures; `_run` delegates into the runner so the migration is
  incremental, and `_probe` remains the bounded, capture-only helper for read-only
  checks, with timeout and error represented at the caller — it is not a
  never-raises contract (the current low-level helper can raise). One shell-out
  policy, as the verbose spec established.
- **D-5 — Theme as central tokens + policy, not a rebuild.** The U1 palette and the
  "no forced background / no dim on essentials" rules live in one place shared by
  the CLI and any future TUI. The presenter consumes tokens; it does not own
  lifecycle state, and there is no global theme-engine swap.
- **D-6 — Explicit JSON mode.** Machine output is chosen by the `--json` flag,
  never inferred from tty-ness or where stderr points. Rationale: `profile apply
  --json` must stay parseable even when stdout is a real terminal (see the
  [`profile-apply`](../profile-apply/specifications.md) spec). Human diagnostics go
  to stderr; the stdout payload is reserved for explicit JSON/eval modes — that
  diagnostics move to stderr does not mean *all* output does.
- **D-7 — Single terminal owner, cadence-based refresh.** Exactly one component
  owns the screen at a time: `Live` stops before any prompt and is resumed only if
  still needed; elapsed/idle recompute once per refresh cadence, independent of
  output arrival (E-6's baked-timestamp fix).
- **D-8 — Optional TUI behind a spike gate.** Compare **Textual** in an isolated
  optional extra / throwaway prototype against extending the existing
  prompt_toolkit/Rich stack (no new base dep, but more manual focus/layout/refresh
  work). Promote a TUI only if it is demonstrably faster for multi-tool navigation
  AND passes all of: keyboard + contrast accessibility, terminal
  handoff/restore and cancellation without regressions, JSON/eval contracts intact,
  no duplicate execution path, no base-install or startup cost. Otherwise the
  evaluation stops and the CLI stands.
  The dev-only [`prompt_toolkit` fixture spike](../../../dev/tui_preview.py) was
  exercised independently under a PTY. No Textual comparison or timed user
  comparison has run; no promotion, dependency or CLI change was made.
- **D-9 — Authorize the actual command, not `sudo -v` (revised 2026-11-17).**
  Devstuff-owned managed sudo invocations use `sudo -n`; opaque scripts stay
  unchanged but run with disconnected stdin and no controlling tty. There is
  no detached prevalidation or keepalive. `sudo -v` checks or refreshes the
  credential timestamp in its own context, not permission for a particular
  command. A scoped sudoers rule can allow that command with `NOPASSWD` even
  when validation itself fails; using it as a gate can wrongly reject a valid
  run. A successful validation likewise doesn't confer permission or prove
  the isolated child shares the timestamp. Real authentication is possible
  only through explicit `DEVSTUFF_TERMINAL_INPUT=1` passthrough on a real
  terminal, outside machine mode and without `Live`.
- **D-10 — Verify only explicit `check_cmd` postconditions.** Declared
  postconditions are bounded and separate from version display. A failure or
  unavailable result does not count as verified. Existing installed discovery
  remains boolean; this change does not make discovery generally ternary.

## Rejected

- **Default fullscreen / alternate-screen UI** — breaks scrollback, complicates
  copy/paste of the last lines users need to report bugs, and a transient `Live`
  already loses the final frame (E-6); the fix is a durable result block, not more
  screen ownership.
- **Browser / web UI** — the tool is a terminal installer; a web surface adds a
  server, auth questions and a second interaction model for no reported need.
- **Custom terminal engine** — maintaining our own renderer duplicates
  Rich/questionary and compounds the accessibility work for zero leverage.
- **PTY-for-every-script** — a pty would make hidden prompts *possible* to answer
  but invites scripts to pollute capture with control codes and blurs the
  managed/unmanaged boundary; R3 instead gives trusted scripts an explicit opt-in
  passthrough.
- **Per-tool installer subclasses** — the catalog's "data, not code" rule holds;
  Docker's verification needs (R6) are met with richer postcondition checks, not a
  Docker class.
- **Blanket noninteractive env as enforcement** — `DEBIAN_FRONTEND` at the top
  level is filtered by `sudo` anyway (E-3) and doesn't cover dpkg conffile prompts,
  needrestart, or custom scripts; env is applied at the privileged invocation where
  it's actually honored.
- **Global forced timeout** — a real package install legitimately runs for many
  minutes; deadlines exist only where a bound is already defined (probes, hooks,
  downloads, apt refresh — R2). Silence is reported, never punished.
- **Default auto-retry** — retries a privileged package transaction without
  evidence of what failed is how half-installed states are made; R4 reports partial
  state and the next action instead.
