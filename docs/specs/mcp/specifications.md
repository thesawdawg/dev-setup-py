# Specification: `devstuff mcp` — devstuff's tools as an MCP / HTTP tool server

**Date:** 2026-10-11
**Status:** Draft for review. Findings F-1…F-6 were measured on 2026-10-11; nothing is implemented.
**Authors:** Sawyer + Claude
**Roadmap:** new (overlaps [M4](../../ROADMAP.md): the `outdated` agent tool is built once and serves both)
**Builds on:** [`agent/`](../agent/) (the tool registry, `AgentTool.to_schema()` / `bind()` and the
bridges), [`outdated/`](../outdated/) and [`profile/`](../profile/) (the read-only reports).

---

## 1. Problem Statement & Goals

devstuff knows which developer tools exist, which are installed, which are stale and how to install
them. An AI client — Claude Code, Claude Desktop, an Open WebUI instance, a script — has no way to ask
it. It either shells out to `devstuff` and parses tables, or guesses at `apt`/`npm` itself and bypasses
the catalog (its dependency order, its sudo handling, its verification).

**Goal:** `devstuff mcp` serves devstuff's catalog-aware tools to any client that speaks one of three
framings of the same tool set:

1. **MCP over stdio** — `devstuff mcp` (what `claude mcp add devstuff -- devstuff mcp` launches).
2. **MCP over HTTP** — `devstuff mcp --http` at `POST /mcp` (Streamable HTTP, JSON responses).
3. **OpenAPI / REST over HTTP** — the same server, `GET /openapi.json`, `GET /tools` (OpenAI
   function-calling JSON) and `POST /tools/<name>`, for clients that consume OpenAPI tool servers.

**Non-goals** (reasons in `stack-decisions.md`):
- **No workspace tools** (`read_file`, `write_file`, `run_command`, `cd`). Those exist so a local Ollama
  model has hands; every MCP client already has its own, and a second, weaker copy is only a risk.
- **No `profile apply`, `remove` or `update` in v1.** Scope was chosen on 2026-10-11 as: catalog tools,
  `outdated`, `profile diff`, `script` functions. They are additive later; see §5.
- **No MCP resources, prompts, sampling, elicitation, logging or progress notifications.** Tools only.
- **No sessions, no SSE streaming, no JSON-RPC batching.** Every request is answered independently with
  one JSON body (F-3: the real client works without any of them).
- **No OAuth, no TLS.** A bearer token and loopback by default; see §4. A non-loopback bind is possible
  and is warned about, not secured.
- **No new runtime dependency** (SD-1). The official SDK is used only as a test-time client.
- **Not a hosted service.** One process, one user, one machine.

---

## 2. Functional Requirements

### The toolbox

- **FR-1** The toolbox is **derived**, not re-declared: the effective agent toolbox
  (`agent.registry.build()`) restricted to entries whose `impl` is `catalog` or `function`. Primitive
  entries (`read_file`, `write_file`, `list_dir`, `cd`, `run_command`) never appear, and a user's
  `agent_tools.yaml` cannot add a primitive to the MCP surface.
- **FR-2** The v1 tools, by key:

  | Key | Kind | Gated? | Source |
  |---|---|---|---|
  | `list_tools` | catalog | no | existing bridge |
  | `search_catalog` | catalog | no | existing bridge |
  | `tool_info` | catalog | no | existing bridge |
  | `outdated` | catalog | no | **new** bridge over `updates.collect_candidates`/`make_row` (§FR-9) |
  | `profile_diff` | catalog | no | **new** bridge over `profile.compare`/`snapshot.machine_facts` (§FR-10) |
  | `install_tool` | catalog | **yes** | existing bridge |
  | `fn_<key>` | function | **yes** | every `type: script` function, auto-exposed as for the agent |

  `outdated` and `profile_diff` are added to `agent_tools.yaml` and `agent/bridges.py`, so the local agent
  gains them too (this is M4's `outdated` half; `doctor` stays in M4).
- **FR-3** **Gating is enforced at dispatch, not only at listing.** Without `--allow-install`, a gated tool
  (`mutating: true`) is absent from `tools/list`, `GET /tools` and `/openapi.json`, **and** a `tools/call` or
  `POST /tools/<name>` naming it is refused exactly as an unknown tool is. A client that learned a tool
  name elsewhere gets nothing. (Same shape as the agent's rule that the denylist runs before the prompt.)
- **FR-4** Every bridge reachable from the MCP surface must work with no workspace and no agent config
  (F-5). A test calls each non-primitive bridge with `ws=None, config=None`, so a future bridge that starts
  using them fails loudly rather than crashing under a client.

### Calls and results

- **FR-5** Arguments go through `AgentTool.bind()`. A call it rejects (missing required, unexpected
  parameter) is a **tool-execution error** — MCP `isError: true` with the message — so the model can
  correct itself. It is never a protocol error and never ends the session.
- **FR-6** A bridge that raises (`SandboxError`, `ValueError`, an unexpected exception) becomes an
  `isError: true` result with a one-line message. **No exception ever escapes the request handler**: a
  crashing tool cannot end the server. The traceback goes to stderr.
- **FR-7** Results are one `text` content item: the bridge's string, unchanged. `outdated` and
  `profile_diff` return the same JSON array `devstuff outdated --json` / `profile diff --json` print
  (`[r.to_json() for r in rows]`), so there is one documented shape.
- **FR-8** **Mutating calls are serialised**: at most one gated tool runs at a time; a second waits. apt
  holds a lock, installers share prefixes, and `profile apply` already runs serially for the same reason.
  Read-only tools run concurrently.
- **FR-9** `outdated` takes an optional `packages` (list of keys), `updates_only` (bool). It is read-only
  and keeps `outdated`'s honesty rule: an `unknown` or `unsupported` row is returned as such and never
  as `current`.
- **FR-10** `profile_diff` takes `profile` (a bundle `@name`; **not** an arbitrary path) and returns the
  seven-state rows. A path would let a client read any YAML file the user can read; a bundle name is
  looked up by listing the directory (`bundles.py`), never built into a path.

### Process-level safety (the part that makes stdio possible)

- **FR-11** At startup, **before any tool can run**, the server moves its protocol channel onto private
  descriptors and repoints the standard ones (F-1): `dup` the real stdout (and stdin, for stdio mode) to
  private fds; `dup2(2, 1)` so fd 1 is stderr; `dup2(/dev/null, 0)` so fd 0 reads EOF. This is done at the
  descriptor level, not by patching `sys.stdout`, because the leaks are in three places (the Rich
  `console`, `_run`'s verbose branch, `function_runner`) plus every child process.
- **FR-12** Nothing but protocol frames is ever written to the protocol channel — **no stdout path exists
  at all**, rather than a check at each call site. A test makes a fake installer write through all five
  routes measured in F-1 and asserts stdout carries exactly the frames.
- **FR-13** An installer that needs a password cannot be answered (stdin is `/dev/null`, there is no
  tty). It must fail with a message that tells the client what to do — `sudo` needs a terminal; run
  `devstuff install <key>` yourself — and must never hang. *The exact failure text is to be measured at
  build time (F-6); this requirement is what that measurement must satisfy.*
- **FR-14** Log lines — startup, per request at `-v`, tracebacks — go to stderr only, through the existing
  `verbose` logger. `-v` therefore also streams installer output, to stderr.

### MCP framing (both transports)

- **FR-15** JSON-RPC 2.0, one message per request. The server answers `initialize`, `ping`, `tools/list`
  and `tools/call`, and ignores `notifications/*` (including `notifications/initialized`). It advertises
  `capabilities: {"tools": {}}` and nothing else.
- **FR-16** **Method not found is a JSON-RPC `-32601` error, not an empty result.** The current official
  client's first request is `server/discover` under protocol `2026-07-28`, and it falls back to
  `initialize` only when told the method is unknown (F-2). `resources/*`, `prompts/*` and any other
  method get `-32601` too.
- **FR-17** **Version negotiation:** the server supports `2025-11-25` (the only version the client was
  measured to send). If `initialize` asks for a version it does not support it answers with its own latest,
  as the spec requires; it never refuses to start a session for a version mismatch.
- **FR-18** A JSON array (batch) is rejected with `-32600`. An unparseable body is `-32700` with `id: null`.
  A `tools/call` for an unknown or gated-off tool is `-32602`. Request bodies over 1 MiB are refused.
- **FR-19** **stdio:** newline-delimited frames, UTF-8, one JSON object per line; a frame may not contain a
  raw newline. EOF on stdin ends the process with status `0`. Notifications get no reply.
- **FR-20** **HTTP:** `POST /mcp` with a JSON body → `200` and a JSON body (`Content-Type:
  application/json`), `202` for a notification. `GET /mcp` and `DELETE /mcp` → `405` (no SSE stream, no
  sessions); the client never sends them (F-3). `Mcp-Session-Id` is never issued.

### REST / OpenAPI framing

- **FR-21** `GET /tools` returns the exposed tools as the OpenAI function-calling array —
  `AgentTool.to_schema()` output, unchanged. (The agent already emits this shape: Ollama's `tools[]` is
  OpenAI's.)
- **FR-22** `GET /openapi.json` returns an OpenAPI 3.1 document describing `POST /tools/<key>` per exposed
  tool, with the `bind()` parameter schema as the request body. It lists only what FR-3 lists.
- **FR-23** `POST /tools/<key>` runs the tool with the JSON-object body as arguments and returns `200`
  `{"is_error": bool, "text": str}` — the same two facts as MCP's result, including for tool failures.
  HTTP error statuses are reserved for the request itself: `400` bad JSON/not an object, `401` no/wrong
  token, `403` forbidden `Host`/`Origin`, `404` unknown **or gated-off** tool, `405` wrong method, `413` too
  large.

### HTTP security (FR-24…FR-28 apply to every HTTP route, including `/openapi.json`)

- **FR-24** **Bearer token on every request.** `Authorization: Bearer <token>` compared in constant time. A
  token is generated per run (`secrets.token_urlsafe(32)`) unless `--token-file PATH` names one. It is
  written to a `0600` file and **its path, not the token, is printed**. The token never appears in a log
  line, a URL, or stdout. Missing or wrong → `401`.
- **FR-25** **Loopback by default.** `--host` defaults to `127.0.0.1`; `--port` defaults to a fixed port
  (`8765`) so a client config survives a restart, and `0` asks the OS. Binding a non-loopback address needs
  `--host` explicitly and prints a warning that there is no TLS and that anyone holding the token can use
  every enabled tool.
- **FR-26** **`Host` and `Origin` are validated**, because a web page can send requests to
  `http://localhost:8765` and DNS rebinding makes the browser's own origin checks meaningless. `Host` must
  be the bound address or `localhost` with the right port. A request with an `Origin` header is refused
  unless it is in `--allow-origin` (repeatable; empty by default). Failure → `403`, checked **before** the
  token, so the response doesn't reveal whether a token is accepted.
- **FR-27** `--allow-install` over HTTP changes nothing about FR-24…26 and adds nothing to them; the token
  is simply the only thing standing between the network and `install_tool`. The startup banner states, in
  one line, which tools are enabled and where.
- **FR-28** No CORS headers are ever sent. Browsers are not a supported client.

### Command surface

- **FR-29** `devstuff mcp [--http] [--host H] [--port N] [--token-file P] [--allow-origin O]...
  [--allow-install]`. `--host/--port/--token-file/--allow-origin` without `--http` is a usage error (exit
  `2`). `--list-tools` prints the tools that *would* be exposed with these flags, as the agent's
  `--list`-style output, and exits `0` without starting a server.
- **FR-30** Exit status: `0` on EOF/SIGINT/SIGTERM; `2` for usage errors; `1` if the HTTP port cannot be
  bound. A signal does **not** abort an in-flight install (killing apt half-way is worse than waiting):
  the server stops accepting new requests and exits when the running call returns. A second signal exits
  immediately.

---

## 3. Findings (measured 2026-10-11)

- **F-1 — three separate routes to stdout, plus stdin.** In the install path: the Rich `console`
  (`ui.py:23`, `Console(highlight=False)`, stdout) used by `ui.warn`/`ui.phase`; `_run`'s verbose branch
  (`generic.py:99`, `subprocess.run(cmd, check=True)` inherits fd 1); `function_runner.py:161` likewise.
  stdin is inherited by every one of them. A scratch program that `dup`s fds 0/1 to private copies and then
  `dup2`s fd 1→stderr and fd 0→`/dev/null` kept the protocol channel to exactly one frame while `print()`,
  `sys.stdout.write`, `os.write(1, …)`, a child's `echo` and a child's `cat` all behaved: every leak landed
  on stderr, and a child that read stdin got EOF immediately without consuming a queued frame.
  *Consequence:* FR-11. An audit of call sites would have to be redone every time someone adds one.
- **F-2 — the real client does not start with `initialize`.** `mcp` 2.3.0's client sends `server/discover`
  with `_meta.io.modelcontextprotocol/protocolVersion: "2026-07-28"` first, then `initialize` with
  `protocolVersion: "2025-11-25"`, then `notifications/initialized`, `tools/list`, `tools/call`. Against a
  stub that answered the unknown method with `{}` it fell back anyway; against one that answered `-32601`
  it also fell back, identically over HTTP and stdio. *Consequence:* FR-16, FR-17 — a server written from
  the older spec's handshake alone would still work, but only because of this fallback.
- **F-3 — what the client needs from HTTP.** Requests are `POST /mcp`, `Accept: application/json,
  text/event-stream`, `Content-Type: application/json`, and after `initialize` an
  `MCP-Protocol-Version: 2025-11-25` header. A plain `application/json` reply sufficed; the client never
  sent `GET` or `DELETE` and never needed `Mcp-Session-Id`. *Consequence:* FR-20 (no SSE, no sessions).
- **F-4 — the SDK is a heavy dependency.** `uv pip install mcp` into an empty venv installed 26 packages
  (pydantic-core, cryptography, starlette, uvicorn, httpx2, …), 32 MB, against devstuff's four runtime
  dependencies. *Consequence:* SD-1.
- **F-5 — the bridges are already workspace-free.** `grep` finds no use of `ws` or `config` in
  `agent/bridges.py`; all four catalog bridges and `run_function` take them and ignore them. *Consequence:*
  FR-4 — a test, so this stays true.
- **F-6 — NOT YET MEASURED:** what a `sudo`-requiring installer does with stdin on `/dev/null` and no tty
  (does it fail fast, with what text, and does the message survive `_run`'s capture?). FR-13 states the
  property the measurement must confirm; M-1 starts by running the real thing under the fd setup of FR-11.
- **F-7 — reusable library entry points.** `outdated` and `profile diff` build their rows with plain
  functions (`updates.collect_candidates`, `make_row`, `sort_rows`; `profile.compare`,
  `snapshot.machine_facts`, `sort_diff_rows`), each row with `.to_json()`. The Click commands are not
  involved, so the bridges don't have to call or fake them.

---

## 4. Security model

| Property | Mechanism | Enforced or advisory |
|---|---|---|
| A client cannot reach a gated tool without `--allow-install` | FR-3: refusal at dispatch | **Enforced** |
| A client cannot reach the filesystem or a shell | FR-1: primitives are not in the toolbox | **Enforced** |
| A client cannot read an arbitrary file through `profile_diff` | FR-10: bundle name only | **Enforced** |
| An installer cannot corrupt or eat the protocol stream | FR-11/12: fd-level isolation | **Enforced** |
| A web page cannot drive the HTTP server | FR-24/26/28: token + `Host`/`Origin` | **Enforced** |
| The client's own approval prompt before `install_tool` | client UX | **Advisory** — not a control |
| A non-loopback bind is safe | FR-25 warns | **Advisory** — no TLS |
| The model installs only what the user meant | none | **Out of scope**; `--allow-install` is the user's choice |

`--allow-install` over stdio trusts the client process that launched the server (it can already run
anything as the user). Over HTTP it trusts whoever holds the token — hence FR-24's file, not stdout.

---

## 5. Open questions

| # | Question | Recommendation | Answer |
|---|---|---|---|
| OQ-1 | Build on the stdlib or the official SDK? | Stdlib; SDK as a test-time client only (SD-1) | Pending confirmation — the 2026-10-11 answer was "http, openapi.json-style, common tool system" and did not choose |
| OQ-2 | Should `--host` accept a non-loopback address at all in v1? | Yes, warned (FR-25) | Pending |
| OQ-3 | Fixed default port `8765` vs OS-assigned? | Fixed, so client configs survive restarts | Pending |
| OQ-4 | Add `update`/`remove`/`profile_apply` as later gated tools? | Yes, under the same flag, each its own PR | Deferred (scope chosen 2026-10-11) |
| OQ-5 | Does CI's Python range include versions the `mcp` test client supports? | Check at M-1; the conformance test skips when the SDK can't be imported | Pending |
| OQ-6 | Should gated tools need `--allow-install` or a finer flag per tool? | One flag; per-tool allow-lists are a later `--allow KEY` if anyone asks | Pending |

---

## 6. Acceptance

The feature is done when: the real `mcp` client completes `initialize` → `tools/list` → `tools/call`
against both transports, a gated tool is invisible *and* uncallable without the flag, a fake installer that
writes to every output route leaves the stdout stream as exactly the frames, and every FR above has a test
that fails when the code it describes is broken (mutation-checked, as for `apply`).
