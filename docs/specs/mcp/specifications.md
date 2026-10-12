# Specification: `devstuff mcp` — devstuff's tools as an MCP / HTTP tool server

**Date:** 2026-10-11
**Status:** Approved 2026-10-11 with OQ-1/2/3/6 resolved as recommended (§5), plus two goals that outrank the
rest (G-1, G-2 below). Findings F-1…F-11 were measured on 2026-10-11 (F-6 closed by F-11); the five points of
the 2026-10-11 PR review are folded in (§5a). **M-1 built 2026-10-12** (the `outdated` / `profile_diff`
bridges, FR-1a's classification in `mcp/core.py`, the FR-4 test, F-12/F-13). **M-2 built 2026-10-12**: the
stdio server (`devstuff mcp`, FR-1–19, FR-29/30 for stdio) with the detach and supervisor measured (F-14); HTTP,
REST and OpenAPI (M-3) and `--print-config` (M-4) are not. **M-3 built 2026-10-12**: `--http`, the token file,
`POST /mcp`, REST and `/openapi.json` (F-15). **M-4 built 2026-10-12**: `--print-config`, the SDK and
`claude mcp list` conformance suites (F-16). The one live `tools/call` through Claude Code (§6, G-1.3) was run
2026-10-12 with the user's consent, over stdio (F-17); the same call over HTTP was not.
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
- **FR-1a** **Every entry is classified by what it runs, never by its key or its `mutating` field** (F-10: a
  user `agent_tools.yaml` can override `install_tool` with `mutating: false`, alias it under another key, or
  write a hand-made function bridge, and all three read as ungated). The classification lives in
  `mcp/core.py`, keyed by `(impl, target)`:

  | Entry | Class | Exposed when |
  |---|---|---|
  | `impl: catalog`, `target` ∈ {`list_tools`, `search_catalog`, `tool_info`, `outdated`, `profile_diff`} | read-only | always |
  | `impl: catalog`, `target: install_tool` | install | `--allow-install` |
  | `impl: function`, any `target`, any key | function | `--allow-functions` |
  | `impl: catalog`, any other `target` | unclassified | **never** — left out, with a warning on stderr (fail closed) |

  A `mutating: true` on a read-only entry **adds** gating (it is treated as the function class); no value of
  `mutating` ever removes it. A test fails when a bridge in `agent.bridges._BRIDGES` has no row here (the
  pattern of `test_every_install_type_has_a_planner`), so adding a bridge forces a decision about its gate.
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
- **FR-3** **Gating is enforced at dispatch, not only at listing, and follows FR-1a's class.** A tool of the
  install or function class whose flag (FR-2) is absent is missing from `tools/list`, `GET /tools` and
  `/openapi.json`, **and** a `tools/call` or `POST /tools/<name>` naming it is refused exactly as an unknown
  tool is. A client that learned a tool name elsewhere gets nothing. Listing and dispatch read **one**
  classified toolbox object, so there is no second path that skips the check. The flags are independent:
  `--allow-functions` alone never exposes `install_tool`, and the reverse. (Same shape as the agent's rule
  that the denylist runs before the prompt.)
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
  (`[r.to_json() for r in rows]`), so there is one documented shape. A result may carry an advisory **note**
  (FR-8a) — kept *out of* that text so the array stays parseable: a second `text` content item over MCP, an
  optional `note` string beside `text` over REST.
- **FR-8** **Mutating calls are serialised**: at most one gated tool runs at a time; a second waits. apt
  holds a lock, installers share prefixes, and `profile apply` already runs serially for the same reason.
  Read-only tools run concurrently.
- **FR-8a** **Probe freshness.** `generic.py`'s `_once` memos (`_uv_tool_versions`, `_uv_outdated_map`, …)
  live as long as the process (F-10), which is right for a CLI that lives seconds and wrong for a server
  that lives days. The server therefore owns their lifecycle, with no request ever blocked:
  1. **Fresh per burst:** when the number of in-flight read-only requests goes from 0 to 1,
     `generic.forget_probes()` runs. Concurrent requests inside a burst still share probes (that sharing is
     what `_once` is for), and a new burst after any idle gap starts clean.
  2. **Clear after every mutation, in `finally`** — including a failure that changed state part-way (as
     `profile apply` already does for the same reason).
  3. **Detect a straddle:** a counter bumped when a gated call starts and again when it ends is read at the
     start and end of every read-only request. If it changed and no gated call is running now, the read is
     re-run once. If one is still running (or the retry also straddled), the result is returned with the
     note `an install is in progress; this may change`.
  A read is **never** blocked by a mutation: an install takes minutes, and a frozen `list_tools` is worse
  than an annotated one. Rejected alternatives are in SD-11.
- **FR-9** `outdated` takes an optional `packages` (an `array` of keys, F-12), `updates_only` (bool). It is read-only
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
- **FR-11a** **The server has no controlling terminal** (F-11: a `/dev/null` stdin does *not* give this —
  `sudo` still prompted on the terminal, and a server spawned by Claude Code could open `/dev/tty`). Before
  FR-11's fd move and before any tool can run, the server detaches at the process level:
  1. `os.setsid()`. When spawned by a client this succeeds.
  2. On `EPERM` (the process is a process-group leader — how a shell starts `devstuff mcp --http`): `fork`;
     the child calls `setsid` and is the server; the original process becomes a **supervisor** that closes
     its copies of fds 0 and 1, forwards `SIGINT`/`SIGTERM` to the child, and exits with the child's status
     (FR-30).
  3. **Verify, fail closed.** Try to open `/dev/tty`. It must fail (`ENXIO`). If it still opens **and** any
     gate flag is on, the server exits `1` with the reason and **starts nothing** — no process that can run
     an installer starts while a terminal is reachable. With no gate flag the server is read-only, so it
     warns on stderr and continues.
  The alternative — `start_new_session=True` at each of the six subprocess call sites — is rejected in
  SD-12: it is the per-call-site audit FR-11 already refuses to depend on. A consequence to accept: a
  detached server no longer receives Ctrl-C from the terminal; the supervisor's forwarding replaces it.
- **FR-12** Nothing but protocol frames is ever written to the protocol channel — **no stdout path exists
  at all**, rather than a check at each call site. A test makes a fake installer write through all five
  routes measured in F-1 and asserts stdout carries exactly the frames.
- **FR-13** An installer that needs a password cannot be answered (FR-11a: no controlling terminal, fd 0 is
  `/dev/null`). It fails fast — measured: `sudo` exits `1` with `sudo: A terminal is required to
  authenticate` (F-11) — and the result is `is_error` with the installer's own error text **plus one
  sentence telling the client that if this needs a password it cannot be answered here, and to run
  `devstuff install <key>` in a terminal**. It never hangs and never writes to a terminal. Measured (F-13):
  sudo's line survives into the error at the default verbosity but **not under `-v`** (the error is then only
  `exit code 1`, the line having gone to stderr), so the sentence is appended to *every* `install_tool`
  failure rather than keyed on that text.
- **FR-14** Log lines — startup, per request at `-v`, tracebacks — go to stderr only, through the existing
  `verbose` logger. `-v` therefore also streams installer output, to stderr.

### MCP framing (both transports)

- **FR-15** JSON-RPC 2.0, one message per request. The server answers `initialize`, `ping`, `tools/list`
  and `tools/call`, and ignores `notifications/*` (including `notifications/initialized`). It advertises
  `capabilities: {"tools": {}}` and nothing else.
- **FR-16** **Method not found is a JSON-RPC `-32601` error, not an empty result.** The current official
  client's first request is `server/discover` under protocol `2026-07-28`, and it falls back to
  `initialize` (F-2). `resources/*`, `prompts/*` and any other method get `-32601` too. This is protocol
  correctness, *not* something either real client enforces: both also fall back on `{}`, and the SDK
  waits about five seconds and falls back when the request is never answered (F-16). The raw-frame
  tests are therefore the only ones that can fail on it.
- **FR-16a** **A request's `id` is echoed back unchanged, type included.** Claude Code sends a *string* id
  (`"server-discover-probe-1"`) and an integer **`0`** for `initialize` (F-8). A request is a notification
  only if the key `id` is *absent* — never because its value is falsy — or the first real reply is
  silently dropped. A test sends `id: 0`, `id: ""` and a string id.
- **FR-16b** **Tool names match `^[a-zA-Z0-9_-]{1,64}$`**, the constraint Claude's tool API applies once a
  client prefixes them (`mcp__devstuff__<key>`). Every current key passes (F-9); `registry.build()` output
  is checked in a test, and a user catalog entry that breaks it is left out of the MCP toolbox with a
  warning on stderr rather than breaking the client's whole `tools/list`. The reserved word
  `openai_tool_list` (FR-22) is treated the same way.
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
- **FR-22** `GET /openapi.json` returns an **OpenAPI 3.1.0** document with two kinds of operation — the
  **tool operations** (one per exposed tool) and **one catalog operation** (`GET /tools`).

  Each **tool operation** is `POST /tools/<key>`:
  - `operationId` is the tool key (what OpenAPI tool clients name a function after), `summary` the tool's
    name, `description` the tool's description — plus, for a gated tool, one sentence saying it changes
    this machine;
  - `requestBody` is the shared FR-16c schema, `required: true` iff the tool has a required parameter
    (FR-23 gives the exact behaviour for each body);
  - `responses`: `200` → a `ToolResult` schema (`{is_error: boolean, text: string, note?: string}`; `is_error`
    and `text` required), and `400`, `401`, `403`, `404`, `405`, `413` → an `Error` schema
    (`{error: string}`).

  The **catalog operation** `GET /tools` has `operationId` **`openai_tool_list`**, no request body, `200` →
  an array of OpenAI function objects (the FR-21 shape) and `401`, `403`, `405` → `Error`. The name is a
  reserved word: it satisfies FR-16b so a client that turns every `operationId` into a function name can,
  and a tool *key* equal to it is left out of the toolbox with a warning, so the two kinds of operation can
  never collide.

  For every operation, every status the server can produce is documented and no other is produced; a
  `bearerAuth` scheme (`type: http`, `scheme: bearer`) applies to all of them. `servers[0].url` is built from
  the `Host` the client used (already validated by FR-26), so a client in a container gets a URL it can
  follow, not `127.0.0.1`. `POST /mcp` is not documented: it is MCP's own protocol, not a REST operation,
  and the document's `info.description` says so. The document lists only what FR-3 lists and is generated
  from the same toolbox object as `tools/list`, never maintained separately.
- **FR-22a — valid.** The document passes `openapi-spec-validator` (OpenAPI 3.1) for each of the four flag
  combinations (neither, install, functions, both). A test breaks a copy of the document and shows the
  validator rejects it, so the check is known to be able to fail (measured, F-9).
- **FR-22b — complete in both directions.** Over the **tool operations only** (`POST /tools/{key}`), for
  every flag combination the *set* of `operationId`s equals the set of names in `tools/list` equals the set
  in `GET /tools`; each tool's request schema is equal in all three. A tool missing from the document **or**
  a tool operation naming no tool fails the test. The catalog operation is checked **separately**: it is
  present exactly once, its `operationId` is `openai_tool_list`, and the set of tool `operationId`s never
  contains it (a test registers a user tool with that key and shows it is left out, not shadowed).
- **FR-22c — truthful.** A test client that reads *only* `/openapi.json` — never the server's code or the
  tool registry — builds a request for every operation from its schema, calls it with the bearer token, and
  asserts that the status is one the document lists **for that operation** and that a `200` body validates
  against **that operation's own** documented response schema (`ToolResult` for a tool operation, the
  function array for `openai_tool_list` — never one schema for all). A second pass sends deliberately bad
  requests (every row of FR-23's body table, an unknown parameter, a wrong token, a bad `Host`) and asserts
  each answer is a documented status. Validity alone cannot catch a document that describes an API the
  server doesn't implement.
- **FR-23** `POST /tools/<key>` runs the tool with the JSON-object body as arguments and returns `200`
  `{"is_error": bool, "text": str}` (plus `note`, FR-8a, when there is one) — the same facts as MCP's result,
  including for tool failures. HTTP error statuses are reserved for the request itself: `400` bad
  JSON/not an object, `401` no/wrong token, `403` forbidden `Host`/`Origin`, `404` unknown **or gated-off**
  tool, `405` wrong method, `413` too large. **The body**, which the document marks required exactly when the
  tool has a required parameter:

  | Body | Tool has a required parameter | Tool has none |
  |---|---|---|
  | absent or empty | `400` | treated as `{}` — runs |
  | `{}` | `200`, `is_error: true` naming the missing parameter | runs |
  | `{…}` with an unknown parameter | `200`, `is_error: true` (FR-5) | `200`, `is_error: true` |
  | `null`, `[]`, `"x"`, `3` | `400` | `400` |
  | malformed JSON | `400` | `400` |

  So `curl -X POST …/tools/list_tools` works with no body, as the document's optional body promises, and a
  tool that cannot run without arguments is never run without them.

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
  immediately (status `128+N`). When FR-11a forked, the supervisor forwards both signals — and **`SIGHUP` as
  `SIGTERM`**, since a closing terminal sends it and the detached server would otherwise be orphaned and keep
  running (F-14) — to the server and exits with its status (a signalled server reads as `128+N`), so a
  terminal's Ctrl-C behaves as above even though the server no longer has the terminal.
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
- **F-6 — what a `sudo`-requiring installer does with stdin on `/dev/null`: closed by F-11, and the last
  part measured in M-1 (F-13).** It does *not* fail fast unless the process also has no controlling
  terminal.
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
  **Not measured here:** Claude Desktop (not installed); how Claude renders `isError`. (`tools/call` through
  Claude Code was measured later: F-17.)
- **F-9 — OpenAPI prototype and tool-name check.** A throwaway generator over the real
  `agent.registry.build()` (non-primitive entries) produced 11 operations — four catalog tools and seven
  `fn_*` — that **validate as OpenAPI 3.1.0** under `openapi-spec-validator`; the same document with one
  operation's `responses` emptied is rejected, so the validator can fail. All 11 keys match
  `^[a-zA-Z0-9_-]{1,64}$` (FR-16b) and no description is over 1000 characters. *Consequence:* FR-22a is a
  check against a measured-working tool, not an aspiration. Not measured: any real OpenAPI *consumer*
  (Open WebUI is not installed).
- **F-10 — the permission label and the probe memos, measured against the real code (PR review).**
  (a) With a user `agent_tools.yaml` overriding `install_tool` (`mutating: false`), aliasing `setup` →
  `install_tool`, and hand-writing `runfn` → `aws-saml-reauth`, `registry.build()` reports all three
  `mutating=False`; only the auto-exposed `fn_aws_saml_reauth` stays `True`. `agent/catalog.py` checks only
  that the field is a bool. A gate on that label is a gate the user's file controls. *Consequence:* FR-1a.
  (b) `_uv_tool_versions` and `_uv_outdated_map` (`generic.py`, built on `_once`) are process-lifetime memos
  and the `install_tool` bridge never calls `forget_probes()`. `cache_clear` takes the lock a running probe
  holds, so a clear cannot be overwritten by a probe already in flight — the hazards are staleness across
  idle time, after our own install, after a partial failure, and a read that sees one memo from before a
  mutation and another from after. *Consequence:* FR-8a. Not reproduced as a running server; established by
  reading `_once` and `forget_probes` and by the lock argument above.
- **F-11 — a `/dev/null` stdin is not "no terminal" (PR review; under a pty, fd 0 = `/dev/null`).**

  | Setup | Result |
  |---|---|
  | server process; a plain child of it | `/dev/tty` opens |
  | child with `start_new_session=True` | `/dev/tty` fails, `ENXIO` |
  | `sudo -k true`, plain child | **prompted for a password on the terminal** |
  | `sudo -k true`, `start_new_session=True` | exits `1` at once: `sudo: A terminal is required to authenticate` |
  | stdio server spawned by Claude Code 2.1.296 (`claude mcp list` under a pty — not an interactive session) | `stdin_isatty: false`, **`/dev/tty` reachable** |
  | `os.setsid()` in the server | succeeded and removed `/dev/tty` when spawned by Claude Code; **`EPERM` from an interactive shell** |

  *Consequence:* FR-11a and FR-13. Not measured: an interactive Claude Code session (only `mcp list`);
  a client launched from a desktop with no terminal at all (there `/dev/tty` is absent anyway); the fork
  supervisor itself — measured in M-2 (F-14).
- **F-14 — M-2, measured against the built server (2026-10-12).**
  *Detach.* Under a real pty (a session leader with a controlling terminal, as a shell hands it): the
  process-group-leader case raised `EPERM` from `setsid`, forked, and the child had its own session, a
  different parent and **no reachable `/dev/tty`**; the same probe without the detach **could** open it (the
  control). In the detached child `sudo -k` then `sudo true` exited non-zero at once with `A terminal is
  required to authenticate` and never prompted. The supervisor forwarded `SIGINT`/`SIGTERM`, mirrored the
  server's exit status (7), and read a `SIGKILL`ed server as 137. *Not covered:* an interactive Claude Code
  session, and a client launched with no terminal at all.
  *Decisions the spec did not make:* (1) the supervisor also maps **`SIGHUP` → `SIGTERM`** (FR-30 above);
  (2) each request runs on its own thread, because FR-8 says a read is never blocked and a stdio client sends
  a `tools/list` while an install is still running on the same pipe; (3) EOF waits for requests still
  running, then exits `0` (FR-30's "do not abort an install", applied to a client that went away); (4) the
  channel's write end is **unbuffered**: a frame a dead client never took must not sit in a buffer and fail
  again at interpreter exit; (5) a stdio line over 1 MiB is answered `-32600` with `id: null` and the rest of
  the line is discarded, so its tail is not read as the next frame (FR-18 only says "refused"); (6)
  `InstallFailed` (a `SandboxError` subclass raised by `bridges.install_tool`) is how the server tells an
  installer failure from "unknown tool" when it adds FR-13's hint.
  *Clients.* The official SDK client (`mcp` 2.3.0) completed `server/discover` → `initialize` →
  `tools/list` → `tools/call` over stdio: 5 tools read-only, 13 with both flags; `install_tool` refused as an
  unknown tool without its flag, bad arguments a tool error. Claude Code 2.1.296 `claude mcp list` reported
  `✔ Connected` for the stdio server.
- **F-15 — M-3, measured against the built server (2026-10-12).**
  *The OpenAPI validator is necessary, not sufficient (FR-22a).* `openapi-spec-validator` 0.9.0 rejected a
  document with no `info`, a duplicate `operationId`, a schema `type: bogus`, a dangling `$ref`, a status key
  `99`, and a response with no `description`. It **accepted** a top-level `security` entry naming a scheme
  that does not exist — so the document test checks that itself — and, as OpenAPI 3.1 allows, an operation
  with **no `responses` at all**, which is why the validator's control breaks things it demonstrably rejects.
  Validity says nothing about truth, which is what FR-22c's client is for.
  *A hang found by a test:* a client that leaves a keep-alive connection open made `server_close()` wait on
  that connection's thread for ever, so SIGTERM never ended the server. FR-30 says wait for running *calls*, not
  idle sockets: connections are now tracked as idle (inside the request-line read) or busy; shutdown wakes the
  idle ones, and a busy one is told `Connection: close` after its response.
  *Decisions the spec did not make:* (1) a request carrying `Transfer-Encoding` (a chunked body) is `400`, not
  guessed at; (2) a rejected request's unread body is read and discarded (up to 16 MiB, else the connection is
  closed) *before* the answer is sent, so its bytes are never parsed as the next request and a client that sent a large
  refused body gets the `413` instead of a connection reset (**measured**: without this, a client sending 8 MB
  got `BrokenPipe` and never saw the status; with it, bodies up to the 16 MiB bound are answered, above which
  the client may still see a reset); (3) a path under `/tools/` is `405`
  for every method but `POST` **whether or not the tool exists**, so a gated-off tool and a made-up one are
  indistinguishable; (4) `HEAD`, `OPTIONS`, `PUT`, `PATCH` are `405`, so a CORS preflight is simply a method nobody
  supports; (5) `404` is documented on every tool operation but cannot be reached from the document's own
  paths — every path in it is in the toolbox — so it exists for a client holding an older document after a
  restart with different flags, and the truthfulness test names that one exception rather than hiding it; (6)
  `--allow-host` takes a bare name and `--allow-origin` an exact origin (`http://localhost:3000`, no trailing
  slash), both validated as usage errors; (7) a token file with loose permissions is refused **even with
  `--rotate-token`**, because quietly fixing the mode would hide that the old secret was exposed, and a
  hand-written token under 16 characters is refused; (8) `Host` must carry the bound port, so an SSH tunnel has
  to use the same local port (`-L 8765:127.0.0.1:8765`) and a Docker port mapping to a different host port needs
  that port to be the bound one; (9) `serve_forever` polls every 0.1 s instead of 0.5 s, which is how long a
  shutdown would otherwise take to be noticed.
- **F-16 — M-4: what the real clients do and do not enforce (2026-10-12; `mcp` 2.3.0, Claude Code 2.1.296).**
  *Neither client fails on a wrong answer to `server/discover`.* With `{}` as the answer both fall back to
  `initialize` and connect; with **no answer at all** Claude Code still connects at once and the SDK waits about
  five seconds per connection and then falls back. So `-32601` (FR-16) is protocol correctness that only raw-frame
  tests can hold — a mutation answering `{}` survived every real-client test and was killed by the raw-frame ones.
  *Two SDK clients, one honest test.* The lower-level `ClientSession` never sends `server/discover`; the
  high-level `mcp.Client` does, then `initialize`. A first draft of the conformance suite used `ClientSession` and
  therefore never exercised the fallback at all, which is why the suite uses `Client` and keeps one `ClientSession`
  case for the initialize-first path.
  *`claude mcp list` needs no terminal:* run with stdin `/dev/null` and piped output under an isolated `HOME`, it
  prints one `name: target - <health>` line per server — `✔ Connected`, or `✘ Failed to connect — …` carrying the
  reason (`Server rejected the configured Authorization header (HTTP 401)…` for a wrong token, `ENOENT … posix_spawn`
  for a missing command). Both failures are tested as controls, so `✔ Connected` is shown to be able to be absent.
  *The printed snippets run as printed:* the `--print-config claude-code` stdio command, with and without the gate
  flags, and the HTTP command with its `$(cat <token file>)` header, were each pasted through `bash -c` into a fresh
  `HOME` and then listed `✔ Connected`.
  *Decisions the spec did not make (FR-31):* (1) the launcher is the console script's own absolute path when
  `argv[0]` is a real, executable `devstuff` (a symlink keeps its path: the venv behind it is rebuilt, the link is
  not), otherwise `<this interpreter> -m dev_setup` — never a bare `devstuff` and never a `which` that could name a
  different install; (2) `claude-desktop` with `--http` and `openapi` without `--http` are usage errors, since
  Desktop launches a command over stdio and the OpenAPI document exists only on the HTTP server; (3) `--port 0`
  cannot be printed (nobody knows the port yet); (4) a wildcard `--host` (`0.0.0.0`, `::`) prints `127.0.0.1` and
  says so on stderr; an IPv6 literal is bracketed; (5) the gate flags appear in stdio snippets only — for HTTP they
  are the *server's* launch flags, which a client cannot set, so they are named in a stderr note giving the line
  that starts that server; (6) the snippet is the only thing on stdout and notes go to stderr, so
  `--print-config … > file` is clean; (7) nothing is started, no token file is created or read, and the path is
  printed absolute — a missing file gets a stderr note that the server creates it on first start; (8)
  `--print-config` with `--list-tools` is a usage error, since each prints and exits.
  *Not measured:* Claude Desktop (not installed here, so its snippet is checked for shape only), and the
  expectation that a GUI app does not inherit the shell's `PATH` (it is why the path is absolute, not a finding).
- **F-17 — the live `tools/call` through Claude Code (2026-10-12, 2.1.296, with the user's consent).** One
  `claude -p` turn (real login, so a model turn; $0.053, 3 turns) with a throwaway `--mcp-config` naming
  `devstuff mcp` over **stdio** and `--strict-mcp-config`, allowed tool `mcp__devstuff__tool_info` only, no session
  saved. The `init` event listed the server `connected` and exactly the five read-only tools as
  `mcp__devstuff__*`. The model first called its own `ToolSearch` (`select:mcp__devstuff__tool_info`) to load the
  deferred tool's schema, then `tool_info {"key": "uv"}`; the result came back with `is_error` absent and our text
  verbatim (`key: uv … install type: bash / installed: yes`), and the model answered from it (`bash` install
  script, already installed). Nothing was installed and no gated tool was reachable; the user's own Claude
  configuration was not changed. *Consequences:* G-1.3 is met for stdio. Claude Code lists MCP tools as
  *deferred* and loads a schema on demand, so a tool's name and description are what a model sees first —
  FR-16b's name rule and the descriptions matter more than the schema. *Not measured:* the same call over HTTP
  (the dispatcher is shared and `claude mcp list` connects over it, but a model turn over HTTP was not made), an
  `isError` result rendered by Claude, and any tool that mutates.
- **F-12 — the agent catalog had no list-typed parameter (found building M-1).** `outdated`'s `packages`
  (FR-9) is a list of keys, and `agent/catalog.py`'s `PARAM_TYPES` was `string`, `integer`, `number`,
  `boolean`. Decision: add `array` — always an array of strings, advertised as
  `{"type": "array", "items": {"type": "string"}}` (a bare `{"type": "array"}` validates but says nothing
  about the elements, and OpenAPI 3.1 tooling expects `items`). `AgentTool.bind()` checks parameter
  *names*, never types, so the **bridge** is the type check: a bare string is refused rather than iterated
  into one-letter keys (`_string_list` in `agent/bridges.py`). Rejected: a space-separated string (lossy,
  and a schema-driven client would not know the separator). *Consequence:* `agent_tools.schema.json` and
  `agent/catalog.py` change with the bridges, in the same PR; the local agent gains the type too.
- **F-13 — sudo's line through the real `_run` (M-1, `setsid`, stdin `/dev/null`, no controlling terminal,
  `sudo -k` first).** *Quiet* (the default): `_run` raises `RuntimeError('sudo: A terminal is required to
  authenticate')` — the line survives into the exception, because `_run` captures stderr and uses it as the
  message. *`-v`*: the exception is just **`'exit code 1'`**; sudo's line went to the server's own stderr
  and is not in the result. *Consequence:* FR-13's sentence cannot depend on seeing sudo's line; it is added
  to **every** `install_tool` failure (and the line is included when it is there). `sudo -n true` fails
  differently (`interactive authentication is required`), which is why the wording is not matched on.

---

## 4. Security model

| Property | Mechanism | Enforced or advisory |
|---|---|---|
| A client cannot reach `install_tool` without `--allow-install`, or a `fn_*` tool without `--allow-functions` | FR-1a/3: classification by `(impl, target)`, refusal at dispatch, flags independent | **Enforced** |
| A user `agent_tools.yaml` cannot un-gate a tool (override, alias, hand-written bridge) | FR-1a: `mutating` can only add gating; unclassified targets are not exposed | **Enforced** |
| An installer cannot prompt on, or hang on, the client's terminal | FR-11a/13: detach, verify, refuse to start with a gate flag if `/dev/tty` still opens | **Enforced** (fail closed) |
| A read does not silently report pre-install state | FR-8a: fresh per burst, clear after mutation, retry on straddle | **Enforced**; an overlap that cannot be retried is *annotated*, not hidden |
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

**Known limit, stated rather than fixed.** The read-only tools run the catalog's `check_cmd` shell probes
(`generic.py`, `_check_cmd_installed`). A client that can write `~/.config/devstuff/tools.yaml` can therefore
get shell run by calling `list_tools` without any flag. That file is the user's own and a client able to
write it is already past the user's boundary — the same trust line as editing `.bashrc` — so FR-1a protects
the *gates*, not the catalog. Found by reading, not reproduced. `devstuff agent` has the same two weaknesses
and is out of scope here.

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

## 5a. PR review of 2026-10-11 — resolved

A review of commit `022b29f` raised five points. Each was checked against the code (three were also
measured) and all five held; the review's priorities were adjusted (the terminal point first, since it is the
one that puts a password prompt under the client's UI). Decided 2026-10-11 as recommended, including the two
choices that had alternatives.

| # | Point | Resolution | Where |
|---|---|---|---|
| 1 | Gating followed editable `mutating` metadata | Classify by `(impl, target)`; fail closed on unknown targets; a forcing test over `_BRIDGES` | FR-1a, FR-3, F-10a, SD-10 |
| 2 | Process-lifetime probe memos in a long-lived server | Fresh per burst, clear in `finally` after every mutation, retry once on a straddle. **Chosen over blocking reads during an install** (a frozen `list_tools` for minutes) | FR-8a, FR-7, F-10b, SD-11 |
| 3 | FR-22b/22c contradicted FR-22 over `GET /tools` | Equality over tool operations only; the catalog operation is checked separately; per-operation response schemas | FR-22, 22b, 22c |
| 4 | Optional request body undefined | Absent ≡ `{}` only when nothing is required; a table covering every body | FR-23 |
| 5 | `/dev/null` stdin ≠ no terminal | Process-level detach with a fork-supervisor fallback and a fail-closed `/dev/tty` check. **Chosen over `start_new_session=True` at each call site** (SD-12) | FR-11a, FR-13, FR-30, F-11 |

One correction to the review's own mechanism for #2: a clear cannot be overwritten by an in-flight probe
(they share `_once`'s lock), so the hazards are the staleness and mixed-era cases in FR-8a, not a lost clear.
One detail the review left open: the "install in progress" line is a *separate* note (FR-7), so a result
that is a JSON array stays one.

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
   consent (it makes a model turn), reported separately from the automated checks. **Done 2026-10-12, stdio (F-17).**
4. The snippets from `--print-config claude-code` run as printed and connect. The `claude-desktop` snippet
   is checked for shape only — Desktop is not available here, and the report says so.
5. A request with `id: 0`, `id: ""` and a string id each get a reply with the same id and type (FR-16a).

**G-2 — OpenAPI:** FR-22a (valid), FR-22b (complete both ways, all four flag combinations) and FR-22c
(a client driven only by the document gets documented statuses and schema-valid `200` bodies, and every
bad request gets a documented status).

**And:**
- a gated tool is invisible *and* uncallable without its own flag, and a fake installer that writes to every
  output route leaves stdout as exactly the frames;
- **the exact user file from F-10a** (override, alias, hand-written function bridge) is loaded under all four
  flag combinations and none of its entries is exposed or callable beyond what its `(impl, target)` allows;
  the forcing test fails when a bridge is added to `_BRIDGES` without a classification;
- **probe freshness (FR-8a):** a fake machine changed between two calls is seen by the second; an install
  that fails *after* changing state is seen by the next read; a slow fake probe overlapping a mutation makes
  the retry fire, and one that cannot be retried returns the note;
- **no terminal (FR-11a):** under a pty, with each gate flag on, `/dev/tty` is unreachable from the server and
  from a child, and a real `sudo -k true` run by the install path fails fast with sudo's own message and
  does not prompt; with detach forced to fail (an injectable hook, so the guard is reachable) a gate flag
  makes the server exit `1` and start nothing, and no gate flag makes it warn and serve;
- **OpenAPI bodies:** every row of FR-23's table, for one tool with a required parameter and one without.
