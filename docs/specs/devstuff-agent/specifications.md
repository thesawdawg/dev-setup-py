# Scoped devstuff assistant — specifications

Status: implemented, experimental. Evaluated against the existing `agent/`
spec where legacy behavior is unchanged; this document covers the scoped
default introduced after it.

## 1. Problem

The catalog-only TUI has no purpose on its own: the model-facing agent that
exists is the *workspace* agent — a filesystem/shell kit that answers "find me
a tool for X" with `run_command` and `write_file`, far more authority than the
task needs. The user wants one devstuff assistant that can find tools, read
their catalog definitions and docs links, run named catalog actions, and
propose new catalog entries — without inheriting the workspace agent's blast
radius.

## 2. Design

- **D1 — Two modes, one entry surface.** Plain `devstuff agent` and
  `devstuff tui --chat` share the *scoped* session: the same Ollama
  configuration and preflight, the same fixed tool set, the same per-action
  approval. The legacy workspace agent keeps its exact behavior but is only
  reachable through an explicit opt-in: `--workspace` (interactive picker) or
  `--dir PATH`. `--yolo` exists only for the legacy mode and is rejected in
  scoped mode, where actions always ask. (D1 originally made plain `devstuff
  tui` open the chat directly; D9 below superseded that — the chat is now an
  explicit opt-in there, never the default.)
- **D2 — A fixed, named toolbox.** Scoped mode exposes exactly six tools —
  `find_tools`, `read_tool`, `install_tool`, `run_function`,
  `draft_extension`, `register_extension` — declared in
  `agent/scoped.py` with `impl="scoped"` purely for shape. There is no shell,
  no filesystem tool, no arbitrary URL fetch, and no `agent_tools.yaml`
  loading in this mode: the scoped session never calls `agent/registry.build()`.
- **D3 — Approval is per action, every time.** Mutating calls
  (`install_tool`, `run_function`, `register_extension`) render the complete
  exact definition plus resolved arguments as markup-free `rich.text.Text`,
  then `ui.confirm(..., default=False)`. There is no Always option, no
  auto_approve, no model-supplied approval flag — `bind()` rejects undeclared
  parameters, so `preapproved`/`yolo` fields fail on shape. Re-running a
  registered action asks again, including inside one model response. With no
  terminal (`--print`, non-tty), mutating calls are refused.
- **D4 — Reads are catalog reads, not machine probes.** `find_tools` and
  `read_tool` answer from `load_effective_catalog` records: names, metadata,
  install scripts, `check_cmd` strings, docs URLs — as *data*. They never call
  `is_installed`, `get_version`, `missing_requires`, or `help_cmd`.
- **D5 — Execution runs the pinned definition.** `install_tool` builds its
  `GenericTool` from the effective-catalog record the user just approved and
  runs `install()` under `runner.machine_mode()` — no controlling terminal,
  no hidden sudo prompt, `generic.forget_probes()` afterward. `run_function`
  resolves named arguments through `function_runner.resolve_params`, shows
  them in the preview, and runs `run_script_function(..., managed=True)`:
  `runner.run` with `DEVNULL` stdin, `SUDO_ASKPASS` stripped, captured
  bounded output, a 120s deadline, `privileged=True` termination policy, and
  no `bash -x`.
- **D6 — Extensions are create-only, drafted then registered.**
  `draft_extension` validates a proposed entry through the real
  `validate_catalog` (package or `type: script` function only), refusing keys
  that already exist in the *fresh* effective catalog — bundled included —
  and returns an ephemeral session draft ID (bounded store, cleared by
  `/reset`). `register_extension` re-checks collision with a fresh read both
  before the preview and after approval immediately before writing, refuses
  symlinked catalog files, saves via `save_user_tool`/`save_user_function`,
  reloads the registry, and consumes the ID. Saving never executes.
- **D7 — Dependency-bearing installs are deferred.** `install_tool` refuses a
  package whose `requires` is non-empty, naming the required keys, rather than
  silently probing or auto-installing dependencies. Packages with no
  requirements install normally. This is the conservative v1 boundary;
  checking `missing_requires` would run arbitrary catalog `check_cmd` text
  outside the approval preview.
- **D8 — Scoped sessions persist nothing.** No transcript, no on-disk prompt
  history (memory-only), drafts in memory only. The legacy session's
  transcript/history behavior is unchanged.
- **D9 — `devstuff tui` is model-free by default; chat is opt-in.** The
  default TUI view (`tui_workspace.py`) is a menu workspace driving the same
  `ScopedActions` object the model uses — `find()`/`definition()` for reads,
  `execute(ToolCall, ...)` for drafts, registration, installs and function
  runs — so validation, previews and approvals are identical to chat; a
  person picks each action instead of a model proposing one. `--chat` (or
  the menu's 'Enable local chat') is the only path that constructs an Ollama
  client; `--browse` remains the standalone catalog browser. `--browse`
  +`--chat` conflict, and `--model`/`--host` without `--chat` are rejected —
  no implicit model startup.
- **D10 — Local chat is pinned to the machine.** `local_chat` sessions never
  run the first-run wizard (a prompted remote endpoint would defeat the
  point). The host must be http(s) on `localhost`/`127.0.0.1`/`::1` — no
  userinfo, path, query or fragment — normalized to `127.0.0.1` before the
  client exists; anything else is a `UsageError`. The transport additionally
  refuses every HTTP redirect, and model names ending `:cloud`/`-cloud` are
  rejected (Ollama cloud models can't run from a local-only session).
  With no `agent.yaml` and no `--model`, the candidate is `qwen2.5:3b` —
  preflight still verifies the daemon actually has it, and existing configs
  are never overridden. `num_ctx` is capped at 4096 for the 8 GB CPU-only
  baseline. The client cannot prove the *server's* configuration, so the
  launcher tells the user to run the daemon with `OLLAMA_NO_CLOUD=1`; none
  of this changes the regular `devstuff agent` remote-host path.
- **D11 — The TUI chat front-end is a provider flow, not a second agent.**
  `agent/chat_setup.py` runs between 'Enable chat' and `run_scoped_chat`:
  a provider menu (Ollama local / OpenAI-compatible endpoint / Back),
  skipped by explicit `--provider`; `--model`/`--host` without `--provider`
  keep their Ollama meaning. Ollama mode lists installed local tags (cloud
  aliases filtered) plus authored hardware-heuristic suggestions
  (`qwen3:1.7b`→`qwen3:8b`, RAM/VRAM thresholds — guesses, not
  measurements), confirms a `ollama pull` (managed `runner.run`, 30 min,
  `OLLAMA_NO_CLOUD=1`) before running it, then verifies via
  `preflight.check`. OpenAI mode validates the base URL (http(s), no
  credentials/query/fragment, `/v1` appended when bare, `/chat/completions`
  rejected), warns-and-confirms before contacting an off-loopback endpoint,
  reads an optional `--api-key-env` *name* (the secret never leaves the
  environment), and picks from `GET /models` or a typed ID. Both paths end
  in `run_scoped_chat` — same scoped session, tools and approvals; the
  OpenAI adapter (`agent/openai_compat.py`) carries the wire differences:
  mandatory unique tool-call ids echoed as `tool_call_id` (ToolCall gained
  an optional `id`; Ollama history shape is unchanged), serialized string
  arguments, no Ollama-only fields. A malformed response raises before any
  call executes; no provider fallback, no saved config or keys.

## 3. Trust model

- The user configures the model host (`agent.yaml` / `--host`). **Inspected
  catalog definitions — including install scripts — and tool outputs are sent
  to that host.** The README warns of this; nothing else leaves the machine
  beyond Ollama traffic. Under `tui --chat` the host is additionally forced
  to loopback (D10), but the daemon's own cloud fallback is server-side and
  unverifiable from the client — hence the `OLLAMA_NO_CLOUD=1` guidance.
- Catalog text, docs URLs, and tool output are data, never instructions (the
  system prompt says so, but the boundary is structural: they never become
  argv except inside the exact definition the user approved).
- Named functions run trusted user catalog scripts *on the user's authority*,
  not in a sandbox — the approval preview shows the exact script and resolved
  arguments before anything executes.
- Model output is untrusted: parameters are bound against a fixed schema,
  kind values checked by equality, JSON arguments capped (16 KiB drafts,
  8 KiB function args) and required to be NUL-free string maps.

## 4. Out of scope (v1)

- Installing packages that declare `requires` (D7), and any dependency
  resolution beyond listing the keys.
- `shell-eval` functions — they exist to mutate the calling shell and have no
  meaning inside a subprocess.
- Editing or deleting catalog entries; drafts are create-only.
- `devstuff mcp` and the workspace agent: unchanged, no newly exposed
  mutations.

## 5. Remaining gaps

- Not run against a live Ollama daemon: every interaction here is driven by
  fake-model tests and unit checks. A real-model session (search quality,
  prompt adherence, argument JSON correctness) is the open validation step.
  The `qwen2.5:3b` first-time candidate and the 4096 `num_ctx` cap are sized
  for an 8 GB CPU-only baseline by choice, not by measurement; whether either
  is adequate is part of that live check. A llama.cpp alternative evaluation
  is deferred.
- `install_tool`'s managed child shares the install-reliability work's
  unverified-live caveat: real sudo policies and real installers have not been
  exercised under this path.
- No `devstuff agent` scoped `--print` transcript anywhere — by design; a
  debugging story for scoped sessions may be wanted later.
