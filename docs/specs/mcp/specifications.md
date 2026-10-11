# Specification: `devstuff mcp` — devstuff's tools as an MCP / HTTP tool server

**Date:** 2026-10-11
**Status:** Approved 2026-10-11 with OQ-1/2/3/6 resolved as recommended (§5), plus two goals that outrank the
rest (G-1, G-2 below). Findings F-1…F-9 were measured on 2026-10-11; F-6 is still open; nothing is implemented.
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

### The two goals that outrank everything else

When a requirement below conflicts with either, the goal wins. Each has a measurable test, and "done" in
§6 means both pass.

- **G-1 — Compatible with Claude.** The real **Claude Code** client (`claude mcp add` / `claude mcp list`,
  measured at 2.1.296, F-8) connects to `devstuff mcp` over **stdio and HTTP**, lists the tools and calls
  them; and **Claude Desktop** can be pointed at the stdio server from a config the tool itself prints
  (FR-31). Compatibility is *demonstrated against the real client*, not inferred from the protocol text:
  F-2 and F-8 are both cases where the real client does something the spec text does not predict.
- **G-2 — OpenAPI coverage.** The OpenAPI document is **valid**, **complete in both directions** (every
  exposed tool is exactly one operation and every operation is an exposed tool) and **truthful** (a client
  driven by nothing but the document can call every operation it describes and get what the document says).
  Valid-but-wrong is the failure mode this guards against; FR-22 and §6 make all three checkable.

**Non-goals** (reasons in `stack-decisions.md`):
- **Not claude.ai's remote custom connectors.** Those need a public HTTPS URL and, as far as I know, OAuth;
  this server offers neither (no TLS, bearer token). Not measured — recorded so nobody assumes it works.
- **Not verified against Open WebUI itself.** G-2 is checked with a validator and a document-driven client
  (FR-22); Open WebUI is not installed here, so "works in Open WebUI" is an expectation, not a measurement.
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

  | Key | Kind | Gated by | Source |
  |---|---|---|---|
  | `list_tools` | catalog | — | existing bridge |
  | `search_catalog` | catalog | — | existing bridge |
  | `tool_info` | catalog | — | existing bridge |
  | `outdated` | catalog | — | **new** bridge over `updates.collect_candidates`/`make_row` (§FR-9) |
  | `profile_diff` | catalog | — | **new** bridge over `profile.compare`/`snapshot.machine_facts` (§FR-10) |
  | `install_tool` | catalog | **`--allow-install`** | existing bridge |
  | `fn_<key>` | function | **`--allow-functions`** | every `type: script` function, auto-exposed as for the agent |

  The two flags are separate because the functions are not installs: today's seven include
  `fn_aws_saml_reauth` (touches AWS credentials) and `fn_acc_check` (launches an AI agent against a URL).
  Letting a flag called "install" switch those on would permit far more than its name promises (OQ-6).

  `outdated` and `profile_diff` are added to `agent_tools.yaml` and `agent/bridges.py`, so the local agent
  gains them too (this is M4's `outdated` half; `doctor` stays in M4).
- **FR-3** **Gating is enforced at dispatch, not only at listing.** A `mutating: true` tool whose flag
  (FR-2) is absent is missing from `tools/list`, `GET /tools` and `/openapi.json`, **and** a `tools/call` or
  `POST /tools/<name>` naming it is refused exactly as an unknown tool is. A client that learned a tool
  name elsewhere gets nothing. The flags are independent: `--allow-functions` alone never exposes
  `install_tool`, and the reverse. (Same shape as the agent's rule that the denylist runs before the prompt.)
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
- **FR-16a** **A request's `id` is echoed back unchanged, type included.** Claude Code sends a *string* id
  (`"server-discover-probe-1"`) and an integer **`0`** for `initialize` (F-8). A request is a notification
  only if the key `id` is *absent* — never because its value is falsy — or the first real reply is
  silently dropped. A test sends `id: 0`, `id: ""` and a string id.
- **FR-16b** **Tool names match `^[a-zA-Z0-9_-]{1,64}$`**, the constraint Claude's tool API applies once a
  client prefixes them (`mcp__devstuff__<key>`). Every current key passes (F-9); `registry.build()` output
  is checked in a test, and a user catalog entry that breaks it is left out of the MCP toolbox with a
  warning on stderr rather than breaking the client's whole `tools/list`.
- **FR-16c** Each tool's `inputSchema` is `{"type": "object", "properties": …}` with `required` **omitted
  when empty** (an empty `required` array is invalid in older JSON Schema drafts and some validators
  reject it) and `additionalProperties: false`, so a client's schema check agrees with `bind()`'s
  unexpected-parameter rule. The same schema object feeds MCP, `GET /tools` and OpenAPI, so the three
  cannot disagree.
- **FR-17** **Version negotiation:** the server supports `2025-11-25` (the only version the client was
  measured to send). If `initialize` asks for a version it does not support it answers with its own latest,
  as the spec requires; it never refuses to start a session for a version mismatch.
- **FR-18** A JSON array (batch) is rejected with `-32600`. An unparseable body is `-32700` with `id: null`.
  A `tools/call` for an unknown or gated-off tool is `-32602`. Request bodies over 1 MiB are refused.
- **FR-19** **stdio:** newline-delimited frames, UTF-8, one JSON object per line; a frame may not contain a
  raw newline. EOF on stdin ends the process with status `0`. Notifications get no reply.
- **FR-20** **HTTP:** `POST /mcp` with a JSON body → `200` and a JSON body (`Content-Type:
  application/json`), `202` for a notification. `GET /mcp` and `DELETE /mcp` → `405` (no SSE stream, no
  sessions). Claude Code **does** send `GET /mcp` with `Accept: text/event-stream` right after
  `initialized`; it treated the `405` as "this server has no stream" and stayed connected (F-8), which is
  the spec's own provision. `Mcp-Session-Id` is never issued. The bearer token (FR-24) is checked on these
  too, since Claude Code sends it on every request.

### REST / OpenAPI framing (G-2)

- **FR-21** `GET /tools` returns the exposed tools as the OpenAI function-calling array —
  `AgentTool.to_schema()` output, with FR-16c's schema rules. (The agent already emits this shape:
  Ollama's `tools[]` is OpenAI's.)
- **FR-22** `GET /openapi.json` returns an **OpenAPI 3.1.0** document. For every exposed tool, exactly one
  operation `POST /tools/<key>`:
  - `operationId` is the tool key (what OpenAPI tool clients name a function after), `summary` the tool's
    name, `description` the tool's description — plus, for a gated tool, one sentence saying it changes
    this machine;
  - `requestBody` is the shared FR-16c schema, `required: true` iff the tool has a required parameter;
  - `responses`: `200` → a `ToolResult` schema (`{is_error: boolean, text: string}`, both required), and
    `400`, `401`, `403`, `404`, `405`, `413` → an `Error` schema (`{error: string}`) — every status FR-23
    can produce is documented, and no other is produced;
  - a `bearerAuth` security scheme (`type: http`, `scheme: bearer`) applied to every operation;
  - `servers[0].url` is built from the `Host` the client used (already validated by FR-26), so a client
    in a container gets a URL it can follow, not `127.0.0.1`;
  - `GET /tools` is documented too. `POST /mcp` is not: it is MCP's own protocol, not a REST operation, and
    the document's `info.description` says so.
  It lists only what FR-3 lists. It is generated from the same toolbox object as `tools/list`, never
  maintained separately.
- **FR-22a — valid.** The document passes `openapi-spec-validator` (OpenAPI 3.1) for each of the four flag
  combinations (neither, install, functions, both). A test breaks a copy of the document and shows the
  validator rejects it, so the check is known to be able to fail (measured, F-9).
- **FR-22b — complete in both directions.** For every flag combination the *set* of `operationId`s equals
  the set of names in `tools/list` equals the set in `GET /tools`; each tool's request schema is equal in
  all three. A tool missing from the document **or** an operation naming no tool fails the test.
- **FR-22c — truthful.** A test client that reads *only* `/openapi.json` — never the server's code or the
  tool registry — builds a request body for every operation from its schema, calls it with the bearer
  token, and asserts that the status is one the document lists for that operation and that a `200` body
  validates against the documented `ToolResult` schema. A second pass sends deliberately bad requests
  (non-object body, unknown parameter, wrong token, bad `Host`) and asserts each answer is a documented
  status. Validity alone cannot catch a document that describes an API the server doesn't implement.
- **FR-23** `POST /tools/<key>` runs the tool with the JSON-object body as arguments and returns `200`
  `{"is_error": bool, "text": str}` — the same two facts as MCP's result, including for tool failures.
  HTTP error statuses are reserved for the request itself: `400` bad JSON/not an object, `401` no/wrong
  token, `403` forbidden `Host`/`Origin`, `404` unknown **or gated-off** tool, `405` wrong method, `413` too
  large.

### HTTP security (FR-24…FR-28 apply to every HTTP route, including `/openapi.json`)

- **FR-24** **Bearer token on every request**, `Authorization: Bearer <token>`, compared in constant time.
  The token is **persistent**: on first use it is generated (`secrets.token_urlsafe(32)`) into
  `$XDG_STATE_HOME/devstuff/mcp-token` (default `~/.local/state/devstuff/mcp-token`; directory `0700`, file
  `0600`) and reused on every later run, so a client's `Authorization` header — like its URL (FR-25) —
  survives a restart. `--token-file PATH` names a different file; `--rotate-token` replaces the contents.
  A token file readable by group or others is refused at startup. **The path, never the token, is printed**;
  the token never appears in a log line, a URL or stdout. Missing or wrong → `401`.
- **FR-25** **Loopback by default; the port is fixed.** `--host` defaults to `127.0.0.1`. `--port` defaults
  to `8765`, so a client config survives a restart; `0` asks the OS. If the port is busy the server exits `1`
  with a message — it **never** falls back to another port, which would break every configured client with
  no explanation. A non-loopback `--host` is allowed (a container's `127.0.0.1` is the container itself, so
  Open WebUI in Docker cannot otherwise reach the server) and prints a warning that there is no TLS and
  that anyone holding the token can use every enabled tool. The README recommends an SSH tunnel to reach a
  loopback server from another machine.
- **FR-26** **`Host` and `Origin` are validated**, because a web page can send requests to
  `http://localhost:8765` and DNS rebinding makes the browser's own origin checks meaningless. `Host` is
  accepted when its port is the bound port **and** its name is `localhost`, an IP address literal, or a name
  given with `--allow-host` (repeatable; e.g. `host.docker.internal`). Any other hostname is refused:
  rebinding needs a *name* the attacker controls, which an address literal is not, and a container reaches
  the host as `host.docker.internal` or a bridge IP, so the check must not hard-code the bound address. A
  request with an `Origin` header is refused unless listed in `--allow-origin` (repeatable; empty by
  default). Failure → `403`, checked **before** the token, so the response doesn't reveal whether a token is
  accepted.
- **FR-27** Neither `--allow-install` nor `--allow-functions` changes FR-24…26 or adds to them over HTTP; the
  token is simply the only thing standing between the network and every enabled tool. The startup banner
  states, in one line, which tools are enabled and where.
- **FR-28** No CORS headers are ever sent. Browsers are not a supported client.

### Command surface

- **FR-29** `devstuff mcp [--http] [--host H] [--port N] [--token-file P] [--rotate-token]
  [--allow-origin O]... [--allow-host H]... [--allow-install] [--allow-functions] [--list-tools]
  [--print-config TARGET]`. The `--allow-install`/`--allow-functions` flags apply to stdio too; the
  HTTP-only flags (`--host --port --token-file --rotate-token --allow-origin --allow-host`) without `--http`
  are a usage error (exit `2`). `--list-tools` prints the tools that *would* be exposed with these flags and
  exits `0` without starting a server.
- **FR-30** Exit status: `0` on EOF/SIGINT/SIGTERM; `2` for usage errors; `1` if the HTTP port cannot be
  bound. A signal does **not** abort an in-flight install (killing apt half-way is worse than waiting):
  the server stops accepting new requests and exits when the running call returns. A second signal exits
  immediately.
- **FR-31 — `--print-config TARGET`** (G-1) prints a ready-to-paste snippet for the flags given, then exits
  `0` without starting a server. Targets: `claude-code` (the exact `claude mcp add` command for stdio, and
  for `--http` the `--transport http … --header "Authorization: Bearer $(cat <token file>)"` form),
  `claude-desktop` (the `mcpServers` JSON for `claude_desktop_config.json`) and `openapi` (the base URL,
  `/openapi.json` URL and header). Commands use the **absolute path** of the running `devstuff` (a GUI
  application usually does not inherit the shell's `PATH`; this is an expectation, not measured here) and
  never contain the token itself.

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

- **F-8 — the real Claude Code client, measured (2.1.296, isolated `HOME`).** `claude mcp add` +
  `claude mcp list` (which performs a real handshake) reported `✔ Connected` for **both** a stdio stub and
  an HTTP stub with a bearer header. Everything it sent, identical over both transports:
  1. `server/discover` — **string id** `"server-discover-probe-1"`, protocol `2026-07-28`, answered `-32601`;
  2. `initialize` — **integer id `0`**, protocol `2025-11-25`, capabilities `roots` + `elicitation`;
  3. `notifications/initialized`;
  4. HTTP only: `GET /mcp`, `Accept: text/event-stream` — answered `405`, and it **stayed connected**;
  5. `tools/list` — id `1`.
  `Authorization` was on every HTTP request including the `GET`; `Host` was `127.0.0.1:<port>`.
  *Consequences:* FR-16 (confirmed by the product we care most about, not only by the SDK), FR-16a (an
  `if not id` would have dropped the reply to `initialize`), FR-20 (`405` on `GET` is fine), FR-24.
  **Not measured:** `tools/call` through Claude Code (needs a model turn and the user's credentials — the
  live check in the development plan does it once, with consent); Claude Desktop (not installed here); how
  Claude renders `isError`.
- **F-9 — OpenAPI prototype and tool-name check.** A throwaway generator over the real
  `agent.registry.build()` (non-primitive entries) produced 11 operations — four catalog tools and seven
  `fn_*` — that **validate as OpenAPI 3.1.0** under `openapi-spec-validator`; the same document with one
  operation's `responses` emptied is rejected, so the validator can fail. All 11 keys match
  `^[a-zA-Z0-9_-]{1,64}$` (FR-16b) and no description is over 1000 characters. *Consequence:* FR-22a is a
  check against a measured-working tool, not an aspiration. Not measured: any real OpenAPI *consumer*
  (Open WebUI is not installed).

---

## 4. Security model

| Property | Mechanism | Enforced or advisory |
|---|---|---|
| A client cannot reach `install_tool` without `--allow-install`, or a `fn_*` tool without `--allow-functions` | FR-3: refusal at dispatch, flags independent | **Enforced** |
| A client cannot reach the filesystem or a shell | FR-1: primitives are not in the toolbox | **Enforced** |
| A client cannot read an arbitrary file through `profile_diff` | FR-10: bundle name only | **Enforced** |
| An installer cannot corrupt or eat the protocol stream | FR-11/12: fd-level isolation | **Enforced** |
| A web page cannot drive the HTTP server | FR-24/26/28: token + `Host`/`Origin` | **Enforced** |
| A token file other users can read is refused | FR-24: mode check at startup | **Enforced** |
| The client's own approval prompt before `install_tool` | client UX | **Advisory** — not a control |
| A non-loopback bind is safe | FR-25 warns | **Advisory** — no TLS |
| The model installs only what the user meant | none | **Out of scope**; the flags are the user's choice |

The allow flags over stdio trust the client process that launched the server (it can already run
anything as the user). Over HTTP it trusts whoever holds the token — hence FR-24's file, not stdout.

---

## 5. Open questions

All six were answered on 2026-10-11: OQ-1/2/3/6 "as recommended" (with the changes noted), OQ-5 by
measurement, OQ-4 deferred.

| # | Question | Answer (2026-10-11) |
|---|---|---|
| OQ-1 | Build on the stdlib or the official SDK? | **Stdlib**; the SDK is a dev-only test client (SD-1) |
| OQ-2 | Should `--host` accept a non-loopback address at all in v1? | **Yes**, warned (FR-25). Reason found while answering: a container's `127.0.0.1` is itself, so loopback-only would leave Open WebUI in Docker unable to reach the OpenAPI front end. This also forced the `Host` rule of FR-26 (IP literals and `--allow-host`, not "the bound address") |
| OQ-3 | Fixed default port `8765` vs OS-assigned? | **Fixed** — *and a persistent token* (FR-24): the first draft kept the URL stable while regenerating the token each run, so a client config broke on restart anyway. Never fall back to another port (FR-25) |
| OQ-4 | Add `update`/`remove`/`profile_apply` as later gated tools? | **Deferred.** Each its own PR, `update` first, behind the same two-flag model. `profile_apply @devops` includes docker, which needs sudo and cannot be answered over MCP |
| OQ-5 | Does CI's Python range include versions the `mcp` test client supports? | **Resolved by measurement:** devstuff needs `>=3.11`, `mcp` 2.3.0 needs `>=3.10`, CI is `uv sync` on one default interpreter with no matrix. The conformance test still skips if the SDK is not importable |
| OQ-6 | One flag for every state-changing tool? | **No — two:** `--allow-install` (`install_tool`) and `--allow-functions` (`fn_*`). The seven functions include `fn_aws_saml_reauth` and `fn_acc_check`, which are not installs. A per-tool `--allow KEY` list stays a later option |

---

## 6. Acceptance

Done means **both goals pass** and every FR has a test that fails when the code it describes is broken
(mutation-checked, as for `apply`).

**G-1 — Claude (real clients, no stand-ins):**
1. `claude mcp add` of the stdio command *and* of the HTTP URL with the bearer header, then
   `claude mcp list` in an isolated `HOME`: both report `✔ Connected` (the F-8 method, run against
   `devstuff mcp`). Skipped, with the reason shown, only when `claude` is not on `PATH`.
2. The official `mcp` SDK client completes `initialize` → `tools/list` → `tools/call` over both transports.
3. One live `tools/call` of a read-only tool through Claude Code itself, run once by hand with the user's
   consent (it makes a model turn), reported separately from the automated checks.
4. The snippets from `--print-config claude-code` run as printed and connect. The `claude-desktop` snippet
   is checked for shape only — Desktop is not available here, and the report says so.
5. A request with `id: 0`, `id: ""` and a string id each get a reply with the same id and type (FR-16a).

**G-2 — OpenAPI:** FR-22a (valid), FR-22b (complete both ways, all four flag combinations) and FR-22c
(a client driven only by the document gets documented statuses and schema-valid `200` bodies, and every
bad request gets a documented status).

**And:** a gated tool is invisible *and* uncallable without its own flag, and a fake installer that writes
to every output route leaves stdout as exactly the frames.
