# Development Plan: `devstuff agent`

**Date:** 2026-07-24
**Based on:** `specifications.md`, `stack-decisions.md`

---

## Stack Summary

| Layer | Choice |
|-------|--------|
| Language/runtime | Python ≥3.11, existing `src/dev_setup` package, `uv` |
| Frontend | Terminal REPL — prompt_toolkit input, Rich output, questionary confirms |
| Backend / API | Local Ollama daemon, `POST /api/chat` via stdlib `urllib.request` |
| Default model | `gemma4:latest` — chosen at the M2 checkpoint by measurement |
| Datastore | YAML catalogs (`agent_tools.yaml`, `agent.yaml`); JSON transcripts on disk |
| Auth | None — local only |
| Hosting / CI | PyPI via existing `publish.yml`; unit tests in existing pytest suite |

(Full reasoning in `stack-decisions.md`.)

## Target layout

```
src/dev_setup/
├── agent/
│   ├── __init__.py
│   ├── config.py          # AgentConfig, ~/.config/dev-setup/agent.yaml
│   ├── ollama.py          # transport: chat(), list_models(), _parse_message()
│   ├── catalog.py         # agent_tools.yaml load/validate/merge (mirrors functions_catalog.py)
│   ├── registry.py        # AgentTool/AgentParam, merge with tools.yaml + functions.yaml bridges
│   ├── sandbox.py         # Workspace, SandboxError, denylist
│   ├── primitives.py      # _PRIMITIVES dispatch dict: read_file/write_file/list_dir/cd/run_command
│   ├── bridges.py         # catalog + function impls
│   ├── loop.py            # run_turn(): model → tool_calls → results → repeat
│   └── session.py         # REPL, slash commands, transcript
├── agent_tools.yaml       # bundled agent-tool catalog
└── commands/agent_cmd.py  # Click command
```

## First Vertical Slice

> `devstuff agent` connects to Ollama, the user types "create a directory called foo and put a
> hello.py in it", and the agent completes it via `run_command` + `write_file` with confirmation
> prompts — inside a workspace root it cannot escape.

**Why this slice:** it retires the single largest unknown in the whole design — *whether a small
local model tool-calls reliably enough to be worth building on*. Everything else (catalog
bridges, slash commands, transcripts) is mechanical once that answer is yes. If the answer is no,
we find out in Milestone 2 rather than after building the full toolbox.

## Milestones

### Milestone 1 — Transport + config + preflight ✅ *(complete — `79eb2e2`, branch `feat/agent-milestone-1`)*
**Goal:** `devstuff agent` opens a REPL that chats with a local model. No tools yet.
**Definition of done:** you can hold a conversation; every failure mode (Ollama absent, daemon
down, model not pulled, timeout) prints a one-line actionable message; `--model`/`--host` work.

| Task | Depends on | Notes |
|------|-----------|-------|
| `agent/config.py` — `AgentConfig` + YAML load with defaults | — | Reuse `CONFIG_DIR` from `catalog.py` |
| `agent/ollama.py` — `chat()`, `list_models()`, `show_model()`, `_parse_message()`, error mapping | — | stdlib urllib; injectable transport for tests; `_parse_message` handles `content` / `thinking` / `tool_calls` |
| `commands/agent_cmd.py` + registration in `cli.py` | config, ollama | `--dir --model --host --yolo --print` |
| Preflight checks (FR-3) incl. `tools` capability via `/api/show` | ollama | Reuse `registry.get("ollama").is_installed()`; on failure list local models that *do* have `tools` |
| Bare REPL loop, `/exit`, `/help`, Ctrl-C/Ctrl-D | — | prompt_toolkit `PromptSession` |
| Unit tests: config precedence, error mapping, `_parse_message` incl. thinking + fallback | all | Fake transport, no daemon |

**As-built notes (deviations from the plan above):**
- `agent/preflight.py` was split out of `agent_cmd.py` to keep the command thin.
- `--dir` and `--yolo` were **not** implemented — both are sandbox concepts with nothing
  behind them until M2, and dead flags are worse than absent ones. They land in M2.
- `--print` and `/reset` were pulled forward from M4; `--print` is what makes the milestone
  testable from a non-TTY shell.
- **Live-run finding:** the daemon ignores `think: false` for lfm2.5 and emits raw `<think>`
  tags in `content`. `parse_message` now strips them (FR-3a). Caught only by running against
  a real daemon — the reason this milestone ends in a live checkpoint.
- Remote hosts verified working (FR-3b); `~/.config/dev-setup/agent.yaml` pins
  `http://192.168.1.69:11434`.
- Model spot-check: `gemma4:latest` gave the only fully correct answer to a factual probe;
  `lfm2.5` was wrong and the most verbose reasoner. Default **stays** `lfm2.5` pending the
  M2 tool-calling checkpoint, which measures the skill that actually matters here.

### Milestone 2 — Sandbox + primitives + confirmation ✅ *(complete)*
**Goal:** the agent can create dirs, run commands, and write files inside a workspace root.
**Definition of done:** Flow A of the spec works end to end; Flow C and Flow D are covered by
passing unit tests.

| Task | Depends on | Notes |
|------|-----------|-------|
| ✅ `agent/sandbox.py` — `Workspace.resolve()`, `SandboxError`, protected paths | M1 | Done. 29 tests: traversal, symlink escape, credential dirs, catalog write-block (FR-14a) |
| ✅ Workspace prompt at launch + `--dir` + risk guard (FR-2, FR-7b) | sandbox | Done. Warns on `$HOME`, system dirs, dirty git repos |
| ✅ `agent/sandbox.py` — command denylist matcher | sandbox | sudo/su, pipe-to-shell, catastrophic rm, system binaries, dd-to-device, fork bomb, redirects out of the workspace |
| ✅ `agent/catalog.py` + `agent_tools.yaml` | M1 | Bundled + user catalog, precedence and fail-loud validation |
| ✅ `agent/registry.py` — `AgentTool`, `to_schema()`, `bind()` | catalog | 13 tools built: 9 catalog + 4 auto-exposed functions |
| ✅ `agent/primitives.py` — `_PRIMITIVES` dispatch dict | sandbox, registry | Mirrors `_INSTALLERS` in `generic.py` |
| ✅ `agent/loop.py` — tool-call loop, `max_iterations`, structured tool errors (FR-20) | registry | Every failure returns to the model; nothing raises out of a turn |
| ✅ `agent/approval.py` — preview, unified diff, yes/no/always | loop | `--yolo` and `auto_approve` both honoured; denylist unaffected by either |
| ✅ Output truncation cap (FR-17) | loop | UTF-8 safe; marks dropped byte count |
| ✅ Tests: 125 across sandbox/tools/loop | all | Scripted fake model client; no daemon needed |
| ✅ **Checkpoint: real tool-call reliability** | all | **Resolved: default switched to `gemma4:latest`.** See below. |

**Checkpoint result (2026-07-24).** Same prompt — *"Create a new python project named
xyz-project… write xyz-project/main.py containing a hello world program"* — run against both
candidates:

| Model | Behaviour | Output valid? |
|---|---|---|
| `gemma4:latest` | `run_command(mkdir)` → `write_file(main.py, 'print("Hello, World!")')` | **Yes** — runs |
| `lfm2.5:latest` | `run_command(mkdir)` → `run_command(echo … > main.py)` → `read_file` | **No** — emitted `print(\\Hello, World!\)`, a syntax error |

The failure is instructive: lfm2.5 *had* `write_file` and chose to shell out instead, then lost
the content to shell-quoting. Two further probes on gemma4: asked to install a package it went
straight to `install_tool` without attempting `sudo`, and asked for `/etc/passwd` and
`~/.ssh/id_ed25519` it was refused by the sandbox under `--yolo` and reported both cleanly.

### Milestone 3 — Catalog & function bridges ✅ *(delivered inside M2)*

The bridges shipped with M2 rather than after it: the tool loop needed something to dispatch to,
and building `impl: catalog` / `impl: function` at the same time as `impl: primitive` avoided
speculating about a dispatch shape twice.
**Goal:** the agent can use devstuff itself; new `functions.yaml` entries become tools for free.
**Definition of done:** Flow B works; adding a `script` function makes it callable with no code change.

| Task | Depends on | Notes |
|------|-----------|-------|
| `agent/bridges.py` — `list_tools`, `search_catalog`, `tool_info`, `install_tool` | M2 | Wrap `registry.py`; inherit stdio for sudo prompts (Risk 7). **No `add`/`delete` bridge** — read-plus-install only (FR-14a) |
| Auto-expose `functions.yaml` `script` entries; exclude `shell-eval` (FR-13) | M2 | Same guard rationale as `run_cmd.py` |
| `expose_functions` / `exclude_functions` config in `agent_tools.yaml` | catalog | |
| System prompt: workspace, cwd, available tools, "prefer install_tool over sudo" | M2 | Keep in one module-level constant |
| Tests: function→tool schema conversion, shell-eval exclusion, bridge dispatch | all | |

### Milestone 4 — Session polish & docs ✅ *(complete)*
**Goal:** shippable.
**Definition of done:** README + `docs` command updated, `--print` works, transcripts land on disk,
`uv run pytest` and `ruff` clean.

| Task | Depends on | Notes |
|------|-----------|-------|
| ✅ Slash commands `/tools /history /cwd /model /reset /help /exit` | M3 | |
| ✅ `--print` one-shot mode (FR-16) | M3 | Refuses mutations without `--yolo`, rather than auto-approving |
| ✅ `agent/transcript.py` (FR-18) | M3 | Flushed after every turn, atomic rename; failures never break the session |
| ✅ README section + `help_cmd.py` entry + CLAUDE.md architecture note | M3 | CLAUDE.md documents the security invariants explicitly |
| ✅ `agent_tools.schema.json` for editor tooling | M3 | Verified present in the built wheel alongside `agent_tools.yaml` |
| ✅ Ruff + full test pass | all | 237 unit tests, 5 live smoke tests |

## Testing Strategy

- **Unit** (`tests/test_agent_sandbox.py`, `test_agent_catalog.py`, `test_agent_loop.py`) —
  path containment incl. symlinks, denylist patterns, catalog validation errors, tool-schema
  generation, loop behaviour against scripted fake model responses (tool call → result →
  final answer, malformed args, unknown tool, iteration cap, user decline).
- **Integration** — `@pytest.mark.integration`, opt-in, requires a running Ollama with a pulled
  model. Asserts Flow A produces the expected files. Excluded from the default suite and from
  the CI canary (no GPU/model in CI).
- **Smoke** (`tests/integration/test_agent_smoke.py`, `make -C dev smoke-agent`) — runs the real
  CLI against a live daemon: preflight, `--print` round trip, the think-tag regression, and the
  two first-run error paths. Runs on the **host**, not in the Docker CI image, which has no
  daemon; skips cleanly when nothing is reachable. `DEVSTUFF_AGENT_HOST` / `DEVSTUFF_AGENT_MODEL`
  point it elsewhere without editing config.
- **End-to-end / exploratory** — manual: run the agent against a scratch dir and attempt Flows
  A–D by hand, including deliberately prompting it toward `sudo` and `../` escapes.
- **Not applicable:** `dogfood` (no web UI).

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `lfm2.5` tool-calls unreliably in practice | M | H | M2 checkpoint measures it before M3/M4 are built; structured errors let it self-correct; three other local models with `tools` are drop-in fallbacks via `--model` |
| Sandbox escape via symlink or crafted path | M | **H** | `Path.resolve()` on every arg, tested explicitly; denylist independent of the root check |
| Confirmation fatigue → permanent `--yolo` | M | H | Per-tool "always this session", `auto_approve` config; denylist survives `--yolo` |
| Ollama response-shape drift across versions | M | M | All parsing in `_parse_message()` with a content-JSON fallback |
| Scope creep toward a general coding agent | M | M | v1 out-of-scope list is explicit; no streaming, no compaction, no sub-agents |
| Two catalog validators (`functions_catalog` / `agent/catalog`) drift | L | M | Accepted duplication per the repo's existing design decision; note it in CLAUDE.md |

## Status

All four milestones are complete on branch `feat/agent-milestone-1` (unpushed). 237 unit tests
and 5 live smoke tests pass; `agent_tools.yaml` and `agent_tools.schema.json` ship in the wheel.

## Immediate Next Action

> Use it. Drive `devstuff agent` interactively for real work — the confirmation UX, the diff
> rendering and the `always-this-session` flow have only been exercised by tests and one-shot
> `--print` runs, never by a human in a REPL. That is where friction will show up.
>
> Then: squash-or-merge to master and let the Commitizen bump workflow version it.

Deferred, in rough priority order: context compaction for long sessions (`/reset` is the current
answer), an `add` wizard for agent tools, `catalog import`/`export` for them, and streaming
token-by-token rendering.
