# Stack Decisions: `devstuff agent`

**Date:** 2026-07-24
**Context:** New subsystem inside the existing `devstuff` CLI (Click + Rich + questionary,
YAML-catalog-driven, no per-tool Python classes).

---

## SD-1 — Agent capability surface

**Decision: native devstuff tools + a workspace-scoped filesystem/shell kit.**

The agent gets `read_file`, `write_file`, `list_dir`, `run_command`, `cd` (all confined to a
workspace root) *plus* devstuff-native tools (`install_tool`, `list_tools`, `search_catalog`,
and every `functions.yaml` entry).

- **Rejected — devstuff-native only:** safe and small, but cannot satisfy the driving use case
  (`mkdir` → `uv init` → edit files). It would be a natural-language menu, not an agent.
- **Rejected — unrestricted shell:** maximum capability, but a small local model with an
  unbounded shell on a dev workstation has no failure floor. A single hallucinated `rm -rf`
  during a distracted `y` is unrecoverable.

**Consequence:** the workspace root is the primary security boundary, so it must be enforced
by path resolution, not by prompt instructions to the model.

## SD-2 — Approval model

**Decision: confirm every mutating tool call; read-only tools run silently.**

- Read-only (`read_file`, `list_dir`, `list_tools`, `search_catalog`) auto-run with a dim
  one-line trace.
- Mutating (`write_file`, `run_command`, `install_tool`, function invocations) print the exact
  command / a unified diff of the file change, then prompt **yes / no / always-this-session**.
- `--yolo` disables prompting for a session; the denylist (SD-5) still applies.

**Rejected — auto-run everything inside the sandbox:** the sandbox bounds *where* damage lands,
not *whether* it happens. Overwriting the user's real source files inside the workspace is the
common case, not the exotic one.

## SD-3 — Ollama transport

**Decision: stdlib `urllib.request` against `POST /api/chat`.**

- `devstuff` ships to PyPI as a globally installed CLI with exactly four runtime deps
  (click, pyyaml, rich, questionary). Tool-calling on `/api/chat` is a single JSON POST with a
  `tools` array; responses carrying `tool_calls` are not usefully streamed anyway. The client is
  ~60 lines we own outright.
- **Rejected — official `ollama` package:** pulls `httpx` + `pydantic` into every devstuff
  install, for schema handling we barely need.
- **Rejected — shelling out to the `ollama` CLI:** the CLI exposes no tool-calling interface.

**Consequence:** we own retry/timeout/error mapping, and we must tolerate Ollama's
tool-call response shape changing across versions — pin the parsing in one function
(`_parse_message`) so drift is a one-place fix.

## SD-4 — Workspace root

**Decision: prompt for the workspace at launch, defaulting to `$PWD`.**

`devstuff agent` asks "Workspace directory:" pre-filled with the current directory; `--dir PATH`
skips the prompt. Every path argument is `Path.resolve()`d and rejected unless it is under the
root — this catches `../` traversal and symlink escapes in the same check.

`cd` is an **agent tool** that moves the agent's tracked cwd within the root, not a shell `cd`.
Each `run_command` is its own subprocess, so a shell `cd` would evaporate on exit — the same
constraint that forced `shell-eval` to exist in the functions subsystem.

- **Rejected — silent cwd:** too easy to launch the agent from `$HOME` by accident and hand it
  your entire home directory.
- **Rejected — config allowlist of roots:** more ceremony than a one-key-Enter prompt buys.

## SD-5 — `run_command` policy

**Decision: confirm-everything plus a hard denylist.**

Denied outright (never offered for confirmation, reported to the model as a tool error so it can
re-plan): `sudo`/`doas`, `rm -rf /` and root-adjacent deletes, writes outside the workspace root,
`curl|sh` / `wget|sh` pipelines, `shutdown`/`reboot`/`mkfs`/`dd of=/dev/*`, and history/credential
paths (`~/.ssh`, `~/.aws`, `~/.config/gh`). Patterns live in `agent.yaml` so they are extensible.

- **Rejected — binary allowlist:** safest, but every new language toolchain becomes a config edit;
  the friction would push the user to `--yolo` permanently, which is strictly worse.
- **Rejected — confirm only:** the confirm prompt is a human attention filter, and human
  attention degrades over a long session. The denylist is the part that doesn't get tired.

**Note:** `install_tool` legitimately needs `sudo` for `apt`-type tools. That runs through the
existing `GenericTool.install()` path, which the user explicitly approved by name — it is not
subject to the `run_command` denylist.

## SD-6 — Tool definitions: YAML catalog, reusing the existing ones

**Decision: a bundled `src/dev_setup/agent_tools.yaml`, user-overridable at
`~/.config/dev-setup/agent_tools.yaml`, plus automatic exposure of `functions.yaml`.**

This matches the repo's central design decision ("catalogs are the source of truth, not Python
classes") and the precedence rules already implemented for `tools.yaml`/`functions.yaml`.

Each entry declares `impl:` — `primitive` (dispatches to a Python callable in a
`_PRIMITIVES` dict, mirroring `_INSTALLERS` in `generic.py`), `catalog` (bridges to
`registry.py`), or `function` (bridges to `functions_registry.py`). The catalog carries the
JSON-Schema-shaped `params` and the `mutating:` flag that drives SD-2.

`functions.yaml` entries are auto-exposed: they already carry `name`, `description`, and typed
`params` — that *is* a tool schema. New functions become agent capabilities for free.

**Constraint discovered during design:** `shell-eval` functions must be filtered out of the agent
toolbox. `register: bashrc` ones can't be run by a subprocess at all, and `register: eval` ones
only emit shell code for the *calling* shell to evaluate — neither has meaning inside an agent
loop. This mirrors the guard already in `run_cmd.py`.

- **Rejected — Python-only tool definitions:** simpler, but breaks the pattern the whole
  codebase is built on and makes the toolbox un-extensible without a release.

## SD-7 — Model configuration

**Decision: `~/.config/dev-setup/agent.yaml`, no hard-coded model requirement.**

```yaml
version: 1
model: lfm2.5:latest
host: http://localhost:11434
temperature: 0.2
num_ctx: 16384
think: false
max_iterations: 12
request_timeout: 120
auto_approve: []
deny_patterns: []   # appended to the built-in denylist
```

`--model` / `--host` override per invocation.

**Default model:** `lfm2.5:latest` — verified on this machine as 8.5B MoE, 128k context, with
`tools` and `thinking` capabilities, and a model-default temperature of 0.2 (which the config
mirrors rather than fights).

**Correction to an earlier assumption:** the concern that Gemma lacks Ollama tool-calling is
out of date. `ollama show` reports `tools` for all four locally available models — `gemma4`,
`granite4.1:8b`, `ornith`, and `lfm2.5`. Model choice is genuinely free here.

**Capability preflight, not guesswork:** `POST /api/show {"model": ...}` returns a
`capabilities` array. Preflight reads it and refuses a model without `tools` by name, rather
than letting the user discover it as a loop that never calls anything.

**`thinking` handling:** models advertising `thinking` return a `message.thinking` field
alongside `content`/`tool_calls`. The transport must parse it without confusing it for the
answer. Default `think: false` — chain-of-thought is latency the tool loop rarely needs — with
`think: true` available in config and rendered dimmed when enabled.

**`num_ctx`:** 16384, not the model's full 128k. Ollama allocates the KV cache to `num_ctx`, and
this is aimed at edge devices; 16k comfortably holds a working session with tool results, and
the value is one config line away for anyone with headroom.

## SD-8 — REPL layer

**Decision: `prompt_toolkit` directly (already a transitive dependency via questionary).**

Gives multi-line input, in-session history, and Ctrl-C/Ctrl-D handling with no new dependency.
Rich renders the assistant output and diffs; `ui.confirm` (questionary) handles approvals so the
prompt styling matches the rest of the CLI.

---

## Summary

| Layer | Choice |
|-------|--------|
| Language/runtime | Python ≥3.11, existing `src/dev_setup` package |
| LLM runtime | Ollama, local, `POST /api/chat` |
| HTTP client | stdlib `urllib.request` (zero new deps) |
| Tool definitions | `agent_tools.yaml` + auto-exposed `functions.yaml` + `tools.yaml` bridge |
| Sandbox | Workspace root, prompted at launch (default `$PWD`), `resolve()`-enforced |
| Safety | Confirm-on-mutate + hard denylist |
| Default model | `lfm2.5:latest` (8.5B MoE, 128k ctx, tools + thinking) |
| Config | `~/.config/dev-setup/agent.yaml` |
| REPL | prompt_toolkit + Rich + questionary |
| CLI surface | `devstuff agent [--dir] [--model] [--host] [--yolo] [--print]` |
