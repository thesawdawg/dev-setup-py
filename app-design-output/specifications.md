# Specification: `devstuff agent`

**Date:** 2026-07-24
**Status:** Draft — pending sign-off
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

`devstuff` can install, remove, and run a catalog of developer tools, but every capability is
behind a command the user must know by name. Scaffolding a new project is still a manual
sequence: `mkdir`, `cd`, `uv init`, edit `pyproject.toml`, edit `main.py`.

`devstuff agent` opens an interactive session backed by a **local** Ollama model that can call
devstuff's own tools *and* a workspace-scoped filesystem/shell kit, so the user states an intent
in natural language and the agent executes the sequence — with a confirmation gate on every
mutating step. It stays local: no API keys, no network egress of source code, usable offline.

**Success criteria**
- `devstuff agent` → "Create a new python project named xyz-project" produces a working
  `uv`-initialised project with the agent's own file edits applied, in one session, with the
  user approving each mutating step.
- Zero new runtime dependencies in `pyproject.toml`.
- No tool call can read or write outside the chosen workspace root — proven by unit tests, not
  by prompt instructions.
- Adding a new agent capability is a YAML edit (or a new `functions.yaml` entry), not a code
  change — matching how tools and functions already work.

## 2. Users & Personas

| Persona | Description | Primary needs |
|---------|-------------|---------------|
| Solo dev on a fresh Linux box | Already uses `devstuff` to provision tooling | Scaffold and modify projects without memorising per-ecosystem incantations |
| Privacy/offline-constrained dev | Cannot send source to a hosted model | A capable assistant that never leaves the machine |
| Catalog author (the maintainer) | Curates `tools.yaml` / `functions.yaml` | New catalog entries become agent capabilities automatically |

## 3. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | `devstuff agent` MUST open an interactive REPL session against a local Ollama host. | Must |
| FR-2 | On launch the CLI MUST prompt for a workspace directory, pre-filled with `$PWD`; `--dir PATH` MUST skip the prompt. | Must |
| FR-3 | Preflight MUST verify Ollama is installed and reachable, that the configured model exists locally (`GET /api/tags`), and that it advertises the `tools` capability (`POST /api/show`); each failure MUST give a specific remedy (`devstuff install ollama`, `ollama pull <model>`, "is the daemon running?", "<model> does not support tool-calling — try one of: <local models that do>"). | Must |
| FR-3a | The transport MUST parse `message.thinking` separately from `message.content`. Reasoning MUST NOT be mistaken for the final answer, and MUST be hidden by default (rendered dimmed only when `think: true`). **Verified in M1:** some builds ignore `think: false` and emit raw `<think>…</think>` inside `content` with `thinking` empty (observed with lfm2.5), so the transport MUST also strip inline think blocks, including an unterminated one from a truncated response. | Must |
| FR-3b | A non-localhost `host` MUST be fully supported: preflight MUST skip the local `ollama` binary check when the daemon is remote, and MUST tailor its remedies accordingly. | Must |
| FR-4 | The agent MUST run a tool-calling loop: send messages + tool schemas to `POST /api/chat`, execute returned `tool_calls`, append results as `role: tool` messages, repeat until the model returns a plain text answer or `max_iterations` is hit. | Must |
| FR-5 | Tool schemas MUST be generated from `agent_tools.yaml` (bundled, user-overridable) using the same load/validate/merge precedence as `tools.yaml` and `functions.yaml`. | Must |
| FR-6 | Filesystem primitives `read_file`, `write_file`, `list_dir`, `cd` and shell primitive `run_command` MUST be provided, all resolved against the workspace root. | Must |
| FR-7 | Any path argument that resolves outside the workspace root MUST be refused, and the refusal MUST be returned to the model as a tool error so it can re-plan. | Must |
| FR-7a | Credential directories (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`) MUST be refused for read *and* write even when the workspace root would otherwise contain them — a root of `$HOME` must not put an SSH key in bounds. | Must |
| FR-7b | At launch the CLI MUST warn when the chosen workspace is risky — a system directory, `$HOME`, or a git repo with uncommitted changes — and MUST require confirmation before continuing in interactive mode. This is advisory UX, not a security control; FR-7 is the control. | Must |
| FR-8 | Every mutating tool call MUST be shown to the user and confirmed (yes / no / always-this-session) before execution. `write_file` MUST render a unified diff against the existing content (or mark the file as new). | Must |
| FR-9 | A declined tool call MUST return "user declined" to the model as a tool result and continue the session — not abort it. | Must |
| FR-10 | Commands matching the denylist MUST be refused outright, before any confirmation prompt, and MUST NOT be executable via `--yolo`. | Must |
| FR-11 | Read-only tool calls MUST execute without a prompt, printing a single dim trace line. | Must |
| FR-12 | `tools.yaml` MUST be bridged as agent tools: `list_tools`, `search_catalog`, `tool_info`, `install_tool`. | Must |
| FR-13 | `functions.yaml` entries of `type: script` MUST be auto-exposed as agent tools with their catalog `params` as the schema. `shell-eval` entries MUST be excluded (they cannot run in a subprocess context). | Must |
| FR-14 | Model, host, temperature, `num_ctx`, `think`, `max_iterations`, timeout, auto-approve list and extra deny patterns MUST be configurable via `~/.config/dev-setup/agent.yaml`; `--model` and `--host` MUST override per invocation. Defaults: `lfm2.5:latest`, `num_ctx: 16384`, `temperature: 0.2`, `think: false`. | Must |
| FR-14a | The agent MUST NOT be able to modify the user catalogs — no `add_tool`, `delete_tool`, or direct writes to `~/.config/dev-setup/*.yaml`. Catalog access is read-plus-install only. Those paths MUST be denied to `write_file` even when the workspace root would otherwise permit them. | Must |
| FR-15 | The REPL MUST support in-session slash commands: `/tools`, `/cwd`, `/model`, `/reset`, `/history`, `/help`, `/exit`. | Should |
| FR-16 | `--print "<prompt>"` MUST run a single non-interactive turn and exit — usable from scripts. In this mode, absent `--yolo`, mutating calls are refused rather than silently auto-approved. | Should |
| FR-17 | Tool output returned to the model MUST be truncated to a configurable byte cap, with truncation marked, so one `ls -R` cannot blow the context window. | Should |
| FR-18 | The session transcript (messages + tool calls + results) SHOULD be written to `~/.local/share/dev-setup/agent/<timestamp>.json` for debugging. | Should |
| FR-19 | `--yolo` MAY disable confirmation prompts for a session; the denylist still applies and the mode MUST be announced at launch. | May |
| FR-20 | A malformed tool call (unknown tool name, bad JSON arguments, missing required param) MUST be returned to the model as a structured error rather than crashing the session. | Must |

## 4. Non-Functional Requirements

| ID | Category | Requirement |
|----|----------|-------------|
| NFR-1 | Dependencies | No additions to `[project].dependencies`. Transport is stdlib `urllib.request`; REPL uses `prompt_toolkit` already vendored via questionary. |
| NFR-2 | Security | The workspace root is enforced by `Path.resolve()` containment, never by model instruction. Denylist is applied to the resolved argv before any prompt. Credential paths (`~/.ssh`, `~/.aws`) are denied even if the workspace root would otherwise permit them. |
| NFR-3 | Privacy | No network calls other than to the configured Ollama host. No telemetry. |
| NFR-4 | Responsiveness | A spinner is shown while the model is generating; Ctrl-C cancels the in-flight turn and returns to the prompt without killing the session. |
| NFR-5 | Robustness | Ollama connection errors, timeouts, and non-200 responses surface as one-line actionable messages, never tracebacks. |
| NFR-6 | Model portability | Nothing may assume a specific model. Tool-calling support MUST be verified via `/api/show`'s `capabilities` array at preflight, and `thinking`-capable models MUST be handled without special-casing a model name. |
| NFR-7 | Consistency | Output goes through `ui.py`; prompts through questionary; the command lives in `commands/agent_cmd.py` and is registered in `_register_commands`. |
| NFR-8 | Testability | The Ollama transport is injectable so the loop is unit-testable with scripted responses and no daemon. |

## 5. Data Model

- **`AgentConfig`** — `model`, `host`, `temperature`, `num_ctx`, `max_iterations`,
  `request_timeout`, `auto_approve[]`, `deny_patterns[]`, `max_tool_output_bytes`.
  Loaded from `~/.config/dev-setup/agent.yaml`; CLI flags override.
- **`AgentTool`** — `key`, `name`, `description`, `impl` (`primitive`|`catalog`|`function`),
  `mutating: bool`, `params: [AgentParam]`, `target` (for `catalog`/`function` bridges).
  Serialises to an Ollama `tools[]` JSON-Schema entry via `to_schema()`.
- **`AgentParam`** — `name`, `type` (`string`|`integer`|`boolean`), `description`, `required`,
  `default`, `enum`.
- **`Workspace`** — `root: Path` (immutable), `cwd: Path` (mutable, always under `root`);
  owns `resolve(path) -> Path` which raises `SandboxError` on escape.
- **`Session`** — `messages: [dict]`, `workspace`, `config`, `tools: {key: AgentTool}`,
  `always_approved: set[str]`, `transcript_path`.
- **`ToolResult`** — `ok: bool`, `content: str`, `truncated: bool`. Always serialisable back
  into a `role: tool` message, including for errors and declines.

**Relationships:** `Session` owns one `Workspace` and one `AgentConfig`; the tool table is the
merge of `agent_tools.yaml` (bundled → user) + bridged `registry.py` tools + bridged
`functions_registry.py` script functions.

### `agent_tools.yaml` schema (v1)

```yaml
version: 1
expose_functions: true          # auto-expose functions.yaml script entries
exclude_functions: []           # keys to withhold
tools:
  write_file:
    name: Write File
    description: Create or overwrite a UTF-8 text file inside the workspace.
    impl: primitive
    mutating: true
    params:
      - name: path
        type: string
        description: Path relative to the current directory.
        required: true
      - name: content
        type: string
        description: Full file contents.
        required: true
  install_tool:
    name: Install Tool
    description: Install a developer tool from the devstuff catalog.
    impl: catalog
    target: install
    mutating: true
    params:
      - name: key
        type: string
        description: Catalog key, e.g. "uv" or "nvm".
        required: true
```

Validation mirrors `functions_catalog.py`: unknown fields, bad `impl`, unknown `type`, or a
`catalog`/`function` entry with a missing `target` raise `CatalogError` at load time.

## 6. Key User Flows

### Flow A — Scaffold a project (the driving use case)
1. User runs `devstuff agent` in `~/projects`.
2. Prompt: "Workspace directory: [~/projects]" → Enter.
3. Preflight: Ollama reachable, `qwen3:8b` present. Banner shows model, workspace, mode.
4. User: "Create a new python project named xyz-project".
5. Agent calls `run_command("mkdir xyz-project")` → confirm → yes.
6. Agent calls `cd("xyz-project")` → cwd moves (read-only-ish, traced, no prompt).
7. Agent calls `run_command("uv init")` → confirm → yes.
8. Agent calls `read_file("pyproject.toml")` → auto-runs.
9. Agent calls `write_file("src/xyz_project/main.py", ...)` → unified diff shown (new file) → yes.
10. Agent replies in prose summarising what it did.
- **Done when:** `xyz-project/` exists with a valid `pyproject.toml` and the agent's file edits, and the session returns to the prompt.

### Flow B — Provision a missing tool mid-task
1. User: "Set up a Node project here."
2. Agent calls `search_catalog("node")` → auto-runs → finds `nvm`.
3. Agent calls `install_tool("nvm")` → confirm shows the catalog entry's install mechanism → yes.
4. Install runs through the existing `GenericTool.install()` path with its normal output.
- **Done when:** the tool reports installed and the agent continues the original task.

### Flow C — Refusal and re-plan
1. Agent calls `run_command("sudo apt install ripgrep")`.
2. Denylist refuses it before any prompt; the model receives
   `error: 'sudo' is not permitted; use install_tool for catalog tools`.
3. Agent re-plans and calls `install_tool("ripgrep")`.
- **Done when:** the session continues without the user having to intervene.

### Flow D — Escape attempt
1. Agent calls `write_file("../../.ssh/authorized_keys", ...)`.
2. `Workspace.resolve()` raises; the model receives
   `error: path escapes workspace root /home/sawyer/projects`.
3. No prompt is ever shown to the user.
- **Done when:** nothing outside the root was touched. Covered by a unit test.

## 7. Out of Scope (v1)

- Non-Ollama backends (hosted APIs, llama.cpp, vLLM).
- Multi-agent orchestration, sub-agents, or background tasks.
- Automatic context compaction / summarisation of long sessions (`/reset` is the v1 answer).
- Semantic code search, embeddings, or repo indexing.
- An `add` wizard for agent tools (same gap functions already has).
- Editing `agent_tools.yaml` from within the agent (no self-modifying toolbox).
- **Agent-driven catalog authoring** — the agent cannot run `add`/`delete` or write to
  `~/.config/dev-setup/`. Read-plus-install only (FR-14a).
- Windows/macOS support — the project targets Linux.
- Streaming token-by-token rendering (non-streaming `/api/chat` in v1).

## 8. Open Questions & Risks

| # | Question / Risk | Owner | Resolution |
|---|-----------------|-------|------------|
| 1 | Small local models (~8B) tool-call unreliably: invented tool names, args as JSON strings, endless retry loops. | Claude | Mitigated by `max_iterations`, structured errors back to the model (FR-20), and a tight system prompt. **The Milestone 2 checkpoint measures this before more is built.** |
| 2 | ~~"gemma4" may not support Ollama tool-calling.~~ | Sawyer | **Resolved 2026-07-24:** the assumption was wrong. `ollama show` reports `tools` for all four local models (`lfm2.5`, `gemma4`, `granite4.1:8b`, `ornith`). Default is `lfm2.5:latest`; capability is verified at preflight rather than assumed. |
| 3 | Ollama's tool-call response shape varies by version (`tool_calls` vs content-embedded JSON), and `thinking` models add a third field. | Claude | **Confirmed real in M1**, not hypothetical: lfm2.5 returned inline `<think>` tags with an empty `thinking` field. All handled in `parse_message()` — think-block extraction, double-encoded arguments, fenced/bare JSON fallback. Think stripping runs *before* the fallback, or a reasoning preamble would hide every content-embedded tool call. |
| 4 | Confirmation fatigue drives users to permanent `--yolo`. | Sawyer | "Always this session" per tool + `auto_approve` config; denylist survives `--yolo`. |
| 5 | Context fills once tool results accumulate; `num_ctx: 16384` is well under the model's 128k, and raising it costs KV-cache RAM on an edge device. | Claude | Truncation cap (FR-17) + `/reset`; `num_ctx` is configurable for machines with headroom; compaction deferred. |
| 6 | ~~Should the agent be able to call `add`/`delete` on the catalog?~~ | Sawyer | **Decided 2026-07-24: no.** Read-plus-install only; catalog authoring stays a human action (FR-14a). |
| 7 | `install_tool` may hang on an `apt` sudo password prompt inside the agent loop. | Claude | Run catalog installs with inherited stdio so the prompt reaches the terminal; document that `sudo` may be asked for. |
