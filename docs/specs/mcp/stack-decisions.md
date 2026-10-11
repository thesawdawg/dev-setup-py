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

## SD-4 — Two flags for state-changing tools

**Chosen:** `--allow-install` enables `install_tool` and `--allow-functions` enables every `fn_*` function;
absent, the respective tools do not exist (FR-3). Changed from one flag on 2026-10-11: the seven functions
include `fn_aws_saml_reauth` (AWS credentials) and `fn_acc_check` (launches an AI agent), so a flag named
"install" would have permitted far more than its name says.

**Rejected — per-tool consent via the MCP client's approval UI only.** The prompt is a human attention
filter, and attention decays over a session; the agent's denylist and `--yolo` make the same point. A
server-side switch is the only thing that holds when the client is a script, a web UI that auto-approves,
or a model on its hundredth call.

**Rejected — one flag for both** (the first draft; see above).

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

## SD-8 — A persistent bearer token, a fixed port, and a `Host` rule that is not "the bound address"

**Chosen:** FR-24/25/26. The token lives in a `0600` file and survives restarts; the port is `8765`; `Host`
is accepted for `localhost`, IP literals and `--allow-host` names.

**Rejected — a fresh token per run (the first draft).** It kept the URL stable and broke the
`Authorization` header, so a configured client failed on every restart; the stable port bought nothing.

**Rejected — `Host` must equal the bound address.** Under `--host 0.0.0.0` the client's `Host` is whatever
address it used, and from a container it is `host.docker.internal`; the rule would have refused the very
setups OQ-2 exists to allow. DNS rebinding needs a hostname the attacker controls, so refusing unknown
*names* while accepting address literals keeps the protection.

**Rejected — falling back to another port when 8765 is busy.** It would break every configured client
silently; exiting `1` with the reason is the failure that can be understood.

## SD-9 — Prove compatibility against the real clients and the OpenAPI document against itself

**Chosen:** G-1 and G-2 are acceptance tests that run the real thing — `claude mcp list` for Claude Code, the
official SDK client, `openapi-spec-validator`, and a client driven only by `/openapi.json` (FR-22a–c).

**Rejected — hand-written protocol tests only.** They encode the same understanding as the code. F-2 and
F-8 are both cases where the real client behaved in a way the protocol text did not predict (a handshake
that opens with an unknown method; a string id beside an integer `0`).

**Rejected — validating the OpenAPI document and stopping.** A document can be perfectly valid and describe
an API the server does not implement; FR-22c exists because validity says nothing about truth.

---

## SD-10 — Classify tools by what they run, not by their `mutating` label

**Chosen:** FR-1a — a table in `mcp/core.py` keyed by `(impl, target)`; `mutating: true` can add gating and
never remove it; an unclassified catalog target is not exposed; a test forces every bridge in
`agent.bridges._BRIDGES` into the table.

**Rejected — gate on `mutating`, the field the agent already has.** Measured (F-10a): a user
`agent_tools.yaml` overrides `install_tool` with `mutating: false`, aliases it under another key, or writes a
function bridge by hand, and all three read as ungated. The agent can afford that (its prompt and denylist
sit behind the label); a server whose flag *is* the control cannot.

**Rejected — gate on the tool's key.** Closes the override and not the alias.

**Rejected — fix the registry to refuse these entries.** It would change `devstuff agent` for a
reason that is the MCP server's, and the agent's user-override is a feature there. Classification is local to
the surface that needs the guarantee.

**What we give up:** a table to touch when a bridge is added. The forcing test turns that from a hazard into
a prompt.

## SD-11 — Probe freshness by burst, clear and retry; never block a read

**Chosen:** FR-8a — `forget_probes()` when in-flight reads go 0→1 and in `finally` after every mutation; a
mutation counter re-runs a straddling read once; an unretriable overlap is annotated.

**Rejected — block readers while a mutation runs.** Exactly isolated and simple, and an install takes
minutes: `list_tools` would freeze for as long. A stale-but-labelled answer beats a hung one.

**Rejected — a per-request memo (a context variable).** It loses the sharing `_once` exists for: the 8-worker
pool inside one `outdated` call would run 6 `uv tool list` probes instead of 2 (the measurement behind
`_once`).

**Rejected — a time-to-live.** Still serves stale data inside the window, including right after our own
install.

**Rejected — treating "a clear can be overwritten by an in-flight probe" as the hazard.** It cannot:
`cache_clear` takes the lock a running probe holds (F-10b). The real hazards are idle-time staleness, our own
mutations, partial failures and a read that mixes two memos' eras.

## SD-12 — Detach the process from its terminal; do not patch each subprocess call

**Chosen:** FR-11a — `os.setsid()`, with a fork-and-supervise fallback on `EPERM`, then verify `/dev/tty` is
gone and refuse to start with a gate flag if it is not.

**Rejected — `start_new_session=True` on every subprocess.** It works everywhere (F-11) and is less code today.
There are six call sites (`generic.py` four, `function_runner.py` two), `CLAUDE.md` already records that "a
direct `subprocess.run` is a hole", and it is the per-call-site audit FR-11 refuses to depend on for stdout.
The next `subprocess` anyone writes would reopen it silently.

**Rejected — `setsid` alone.** Measured (F-11): `EPERM` from an interactive shell, which is how
`devstuff mcp --http` is normally started.

**Rejected — rely on stdin being `/dev/null`.** `sudo` opens `/dev/tty` directly and prompted on the
terminal (F-11).

**What we give up:** a detached server no longer receives Ctrl-C; the supervisor forwards it. And the
fail-closed check means a server started with a gate flag in an environment where detaching cannot be
verified refuses to run, which is deliberate: the alternative is an installer that can hang a client.
