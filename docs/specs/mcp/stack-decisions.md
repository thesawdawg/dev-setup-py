# Stack decisions: `devstuff mcp`

**Date:** 2026-10-11

Each decision lists what was rejected and why. Only alternatives that were actually weighed against
the code or measured are recorded; nothing here is a retroactive justification of a first guess.

---

## SD-1 — Hand-rolled, stdlib-only transport; the SDK is a test client

**Chosen:** `json` + `http.server.ThreadingHTTPServer` + `sys.stdin`, an estimated 150–300 lines for the MCP core (not yet written, so not measured).
The official `mcp` package is added to the **dev** dependency group only, and the conformance test uses it
as a *client* against our server.

**Rejected — the official `mcp` SDK as a runtime dependency.** Measured (F-4): 26 packages, 32 MB,
including `cryptography` and `pydantic-core` (compiled wheels), against four runtime dependencies today.
`CLAUDE.md` already records why this loses: devstuff is a globally installed CLI, so a dependency is paid
for by every user who never runs `mcp`. The same reasoning chose `urllib` over the Ollama package.

**Rejected — the SDK as an optional extra (`devstuff[mcp]`).** It splits the install path and the docs
(`pipx install devstuff` then `pipx inject …`), and makes `devstuff mcp` a command that sometimes says
"install something first". Not worth it for a protocol this small.

**What we give up:** we own protocol drift. F-2 shows how real that is — the current client opens with a
method from a protocol revision newer than the one it falls back to. The answer is the conformance test:
a third-party client talking to our server catches drift that our own tests, written from the same
understanding as the code, cannot.

## SD-2 — Derive the toolbox from the agent registry

**Chosen:** FR-1 — filter `agent.registry.build()` to `impl in {catalog, function}`.

**Rejected — a separate `mcp_tools.yaml`.** A second catalog means a tool is described twice and the two
drift; the agent subsystem already has validation, user override, `bind()` and the `fn_*` auto-exposure.
Adding `outdated`/`profile_diff` once serves the agent (M4) as well.

**Rejected — expose everything, including primitives, and let the client's prompts gate them.** The
client already has files and a shell. Ours would add a second, differently-confined path to the same
resources, and the agent's sandbox (`Workspace.resolve`) is built for a single workspace root that an MCP
server does not have.

## SD-3 — Isolate stdio at the descriptor level

**Chosen:** FR-11 — private `dup`s of fd 0/1, then `dup2` of stderr onto 1 and `/dev/null` onto 0.

**Rejected — redirect `sys.stdout` and audit call sites.** Measured (F-1): the leaks are the Rich
`console`, `_run`'s verbose branch and `function_runner`, plus every child process, none of which respect a
Python-level redirect. It would also be an audit with no end: the next `subprocess.run` anyone writes
re-opens the hole. The verbose logger solved the same problem the same way, by having no stdout path at
all instead of a check at each call.

**Rejected — run each tool call in a child process and relay its output.** It would isolate everything, at
the cost of a process per call, a serialisation layer for results, and slower `list_tools` (which probes
every tool). The fd move gives the same isolation in-process for one `dup2` call.

## SD-4 — One flag for every state-changing tool

**Chosen:** `--allow-install` enables `install_tool` and every `fn_*` function; absent, they do not exist
(FR-3).

**Rejected — per-tool consent via the MCP client's approval UI only.** The prompt is a human attention
filter, and attention decays over a session; the agent's denylist and `--yolo` make the same point. A
server-side switch is the only thing that holds when the client is a script, a web UI that auto-approves,
or a model on its hundredth call.

**Rejected — a finer `--allow KEY` list in v1.** Nobody has asked, and a flag that takes a list invites a
config file to keep it in. Left as OQ-6.

## SD-5 — Three framings, one dispatcher

**Chosen:** one `dispatch(name, arguments) -> (is_error, text)` that all three framings call; framings
only translate wire formats.

**Rejected — implement REST on top of MCP (or the reverse).** Each would inherit the other's quirks (MCP's
`-32601`-driven handshake, REST's HTTP status vocabulary). A shared dispatcher keeps gating, binding and
error handling in one place, which is where FR-3 and FR-6 live.

## SD-6 — JSON responses only; no sessions, no SSE

**Chosen:** FR-20.

**Rejected — implement Streamable HTTP's SSE mode and `Mcp-Session-Id`.** They exist for server-initiated
messages and resumability, neither of which a tools-only server uses. The measured client (F-3) works
without them. Adding them later is additive; removing them would not be.

## SD-7 — Bearer token in a `0600` file, never printed

**Chosen:** FR-24.

**Rejected — print a URL with `?token=`.** The LAN viewer does this and its own notes say not to paste
the URL into commits or logs; here the secret would also guard `install_tool`. A file path can be logged
safely.

**Rejected — no auth on loopback.** Loopback is reachable by every local user and by any web page the
user visits (DNS rebinding), which is why FR-26 exists as a second layer.
