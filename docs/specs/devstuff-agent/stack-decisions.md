# Scoped devstuff assistant — stack decisions

Each decision lists the alternative weighed and why it lost.

- **D-1: One shared scoped backend, not a TUI-only agent.** `run_agent(...)`
  in `commands/agent_cmd.py` carries model/host/config/preflight once;
  `agent` and `tui --chat` are thin entries over that launcher (TUI
  additionally wires the catalog browser as `/browse`). The default `tui`
  workspace calls the same `ScopedActions` directly, with no model anywhere
  in its path — menus, not a second agent. A separate TUI agent would fork
  the system prompt, the toolbox and the approval policy — the three things
  that must stay identical for "one devstuff assistant". *Rejected:* a
  TUI-specific agent implementation.
- **D-2: Scoped tools ride the existing `AgentTool`/`bind`/schema machinery
  with `impl="scoped"`.** The shape (names, params, enum, JSON-schema export)
  is exactly what the model sees; dispatch goes through
  `loop.execute` → `session.scoped_actions.execute` *before* the legacy
  registry/policy path, so scoped sessions can't accidentally hit a
  workspace primitive. *Rejected:* exposing scoped actions as
  `agent_tools.yaml` entries — that catalog is the workspace toolbox's
  contract and would leak shell/filesystem tools into scope.
- **D-3: Per-action `ui.confirm`, not `ApprovalPolicy`.** The legacy policy
  exists for session-level trust decisions (Always/yolo/auto_approve), all of
  which are wrong for named catalog actions — a registered function's script
  is the thing being approved, not a class of tool. *Rejected:* reusing
  `ApprovalPolicy.confirm`, which can't show the pinned-definition preview
  and offers an "always" the design forbids.
- **D-4: Defer `requires` rather than auto-probe.** `missing_requires` runs
  each dependency's `check_cmd`/`is_installed` — arbitrary catalog script
  outside the approval preview. v1 refuses dep-bearing installs with the key
  list. *Rejected alternatives:* probing silently (executes unapproved
  script text) and showing deps in the preview then probing anyway (the
  probe still runs catalog code the user only saw as metadata).
- **D-5: `function_runner.managed=True` instead of a scoped runner.** The
  shared `run_script_function` owns temp-file creation and arg rendering;
  `managed` swaps only the child launch (`runner.run`, DEVNULL, machine
  mode, 120s, privileged) so existing callers — `devstuff run`, the legacy
  agent bridge — are untouched. *Rejected:* a parallel run path in
  `scoped.py` duplicating the prelude/param logic.
- **D-6: Draft-ID indirection for saves.** The model proposes, the user
  approves the *normalized* record — not the model's raw JSON — and the ID
  is consumed on save. This makes "the exact thing approved" mechanical
  rather than trust-the-model-to-repeat-itself. *Rejected:* approving the
  raw `definition_json` directly (double-validation, and the normalized
  form is what actually lands on disk).
- **D-7: Nothing persisted for scoped sessions.** Transcripts and
  `FileHistory` would record catalog code the user was warned goes to the
  model host — and draft secrets would survive the session boundary they
  were designed to die at. *Rejected:* reuse of the legacy transcript/history
  paths under a flag.
