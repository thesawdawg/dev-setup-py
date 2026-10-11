# Development plan: `devstuff mcp`

**Date:** 2026-10-11
**Spec:** [specifications.md](specifications.md) · **Decisions:** [stack-decisions.md](stack-decisions.md)

## Order of work

Spec → measurement → tests → code → mutation check → live check → docs, one commit per milestone, one PR
targeting `master` from a branch cut **after** this spec merges (never stacked on the spec branch — see
`AGENTS.md`).

| M | Delivers | Needs |
|---|---|---|
| **M-1** | Measure F-6 first. Then `agent/bridges.py` + `agent_tools.yaml`: `outdated` and `profile_diff`; FR-4 test | — |
| **M-2** | `src/dev_setup/mcp/`: `core.py` (dispatch, gating, JSON-RPC handling — pure), `stdio.py` (FR-11/12/19), `commands/mcp_cmd.py` (stdio only) | M-1 |
| **M-3** | `mcp/http.py`: `POST /mcp`, token/Host/Origin (FR-20, 24–28); then REST + OpenAPI (FR-21–23) | M-2 |
| **M-4** | **G-1:** conformance tests (real SDK client; real `claude mcp list`); `--print-config` (FR-31). **G-2:** OpenAPI validity, both-direction coverage and the document-driven client (FR-22a–c). One live `tools/call` through Claude Code, with consent. README, CLAUDE.md section, specs index, roadmap | M-3 |

## Layout

```
src/dev_setup/mcp/
├── __init__.py
├── core.py     # pure: Toolbox (derive + gate), dispatch(), handle_jsonrpc(). No I/O, no sockets
├── stdio.py    # the fd move (FR-11) and the line loop
├── http.py     # ThreadingHTTPServer: auth, Host/Origin, /mcp, /tools, /openapi.json
├── token.py    # persistent 0600 token file, mode check, --rotate-token
├── printconfig.py  # FR-31 snippets
└── openapi.py  # pure: tools -> OpenAPI 3.1 document (same toolbox object as tools/list)
src/dev_setup/commands/mcp_cmd.py
```

`core.py` is pure for the reason `profile.py` and `apply_plan.py` are: the whole decision table — gating,
binding, errors, version negotiation — is tested with hand-built frames and no sockets or processes.

## Testing strategy

**Unit (`tests/test_mcp_*.py`, default run):**
- `core`: one test per FR-3/5/6/15–18 outcome, driven with hand-built JSON-RPC dicts. The gating test
  asserts a gated tool is absent from `tools/list` **and** refused by `tools/call` **and** `POST /tools/x`
  — three assertions, because FR-3 says dispatch, and a list-only check passes when dispatch is open.
- `outdated`/`profile_diff` bridges return the same array as the commands' `--json` for a faked machine;
  an `unknown` row stays `unknown` (the honesty rule, with a test that breaks the renderer to prove it can
  fail, as `outdated` did).
- `http`: a real `ThreadingHTTPServer` on port `0` in-process, exercised with `urllib`. Token, `Host`,
  `Origin` ordering (403 before 401), 405/413, no CORS headers.
- **Tests must be able to reach the guard they claim to test.** The stdout-purity test needs a fake
  installer that writes through *every* route in F-1 — `print`, `sys.stdout`, `os.write(1)`, a child
  `echo`, a child `cat` — run in a subprocess whose stdout the test reads, or it passes whatever the code
  does. A second case runs the same installer *without* the fd move and asserts it does corrupt the
  stream, so the test is shown to be able to fail.

**Dev dependencies added:** `mcp` and `openapi-spec-validator` in the `dev` group only (never runtime).

**Claude Code conformance (`tests/integration/test_mcp_claude.py`, skipped when `claude` is not on `PATH`):**
the F-8 method, run against `devstuff mcp` — isolated `HOME`, `claude mcp add` for stdio and for HTTP with the
bearer header, `claude mcp list`, assert `✔ Connected` for both. No model turn, no credentials.

**OpenAPI (`tests/test_mcp_openapi.py`):** FR-22a validator run (plus a broken copy it must reject), FR-22b set
equality across `tools/list` / `GET /tools` / the document for all four flag combinations, and FR-22c's
document-driven client. Mutation targets: drop a tool from the document, rename an `operationId`, document a
status the server never sends, send one it never documents.

**SDK conformance (`tests/integration/test_mcp_conformance.py`, skipped when `mcp` is not importable):** the
real SDK client — `mcp` in the **dev** dependency group — against `devstuff mcp` over stdio and over HTTP:
`initialize`, `tools/list`, `tools/call` of a read-only tool, and a gated tool absent without the flag and
present with it. This is the test that would have caught F-2.

**Mutation check:** the restore-in-`finally` harness used for `apply` and the progress view — break each
FR's code (remove the gate from dispatch, make `-32601` return `{}`, drop the `dup2`, compare the token
with `==` order-swapped, skip the `Origin` check), confirm a named test fails, and verify the working tree
restores byte-for-byte. Survivors become tests or deleted code.

**Live check (isolated `HOME`):** `claude mcp add` against the real binary if a client is available on the
machine; otherwise the SDK client script, stated honestly as the substitute. A real `install_tool` of a
cheap user-space tool (`uv tool` or `nvm`-free) under `--allow-install` (and a read-only `fn_*` under `--allow-functions` alone), and a sudo-needing one to confirm
FR-13's message. Anything not run live is listed as not run.

## Risks

| Risk | Mitigation |
|---|---|
| Protocol drift: clients move to `2026-07-28` and beyond | Conformance test against the current SDK; FR-16 means a newer client falls back rather than failing; revisit when the fallback stops being offered |
| `dup2` is process-wide and POSIX-only | `devstuff` targets Linux (README); the module refuses to start on a platform without `os.dup2`, with a message, rather than starting unprotected |
| Claude changes its handshake again (it already moved from `initialize` to `server/discover`-first) | The `claude mcp list` test runs against whatever `claude` is installed, so a release that stops connecting fails the suite on the next run; FR-16 means an unknown method degrades rather than errors |
| Claude Desktop can't be run here | The `claude-desktop` snippet is checked for shape only and the report says so; stdio is the transport it uses, and that path is covered by the Claude Code and SDK tests |
| A tool prints to the *original* stdout via a saved file object | The private fd is only reachable from `stdio.py`; a test greps that nothing else holds it |
| HTTP exposes `install_tool` to the network | Loopback default, token, `Host`/`Origin`, the flag, and the one-line banner (FR-25/27); no claim beyond that |
| `list_tools` probes every tool (seconds on a cold machine) and a client times out | Measure at M-2; if slow, cache within one request only — never across requests (a stale "installed" answer is worse than a slow one) |
| The conformance dev dependency fails to resolve on some CI Python | OQ-5; the test skips rather than breaks the suite |

## Out of scope for this plan

`profile apply`, `update` and `remove` as tools (OQ-4); resources/prompts; a per-tool allow-list (OQ-6);
the `doctor` agent tool (M4's other half).
