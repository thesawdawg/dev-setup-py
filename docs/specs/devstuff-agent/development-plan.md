# Scoped devstuff assistant — development plan

## Done in this slice

- [x] `agent/scoped.py`: verbatim scoped `SYSTEM_PROMPT`; six fixed tools
      (`find_tools`, `read_tool`, `install_tool`, `run_function`,
      `draft_extension`, `register_extension`) with the supplied
      descriptions; `ScopedActions.execute` enforcing bind/JSON/approval
      policy; bounded ephemeral draft store; create-only saves with fresh
      collision re-checks and symlink refusal.
- [x] `loop.py`: scoped dispatch ahead of the legacy toolbox/policy;
      markup-escaped tool-error echo.
- [x] `session.py`: `scoped_actions` wiring (fixed tools, scoped system
      prompt, `/reset` clears drafts); `run_repl(..., browse_catalog=...)`
      with `/browse` help + completion, TUI banner note, static-prompt
      fallback for `TERM=dumb`/`DEVSTUFF_PLAIN`, and memory-only history for
      scoped sessions.
- [x] `agent_cmd.py`: shared `run_agent`; legacy workspace mode behind
      `--workspace`/`--dir`; `--yolo` rejected outside it; `--print` scoped
      read-only (no approvals possible); no scoped transcript.
- [x] `tui_cmd.py`: `--browse` keeps the catalog browser (now also the
      `/browse` callback); default delegates to the shared `run_agent` with
      `--model`/`--host` overrides; no `--yolo`.
- [x] `function_runner.py`: `managed=True` routes the function through
      `runner.run` (DEVNULL, machine mode, `SUDO_ASKPASS` stripped,
      64 KiB-bounded capture with a truncation marker, 120s,
      `privileged=True`, no `-x`); existing callers unchanged.
- [x] Review pass: scoped prompts use a foreground-only style +
      `ui._PromptStyle` + `ColorDepth.DEPTH_1_BIT` under `NO_COLOR`; scoped
      interactive requires a real stdout tty (redirected output cannot show
      an approval); `/cwd` hidden for scoped; `/browse` errors report as
      literal text without ending the chat; draft shape checks enforce
      string fields and the per-type install field (`apt_packages` is a
      string, matching how installers `split()` it).
- [x] Tests: `tests/test_agent_scoped.py` (fake-model flow: discovery,
      inspect, draft, single-save register with consumed ID, declined
      install, flag-smuggling refusal, `can_prompt=False`, duplicate-key
      refusal; approved managed function run asserting argv/env/timeout);
      `tests/test_tui_cmd.py` updated (`--browse` coverage + delegation
      smoke proving `tui` hits the shared launcher).
- [x] Model-free TUI workspace (D9/D10): `tui_workspace.py` menus drive the
      same `ScopedActions` (`find`/`definition` public seams, `execute` for
      draft/register/install/run — same validation, previews, approvals;
      declined saves discard the draft). `tui --chat` opts into the scoped
      session with `local_chat`: no wizard, loopback-only host validation
      before the client exists, no-redirect transport, `:cloud`/`-cloud`
      model rejection, `qwen2.5:3b` first-time candidate, `num_ctx` capped
      at 4096, `OLLAMA_NO_CLOUD=1` caveat printed at launch. `--browse`
      standalone preserved; `--browse`/`--chat` and orphaned
      `--model`/`--host` rejected.
- [x] Provider flow (D11): `agent/chat_setup.py` between 'Enable chat' and
      `run_scoped_chat` — provider menu (skipped by `--provider`; bare
      `--model`/`--host` keep their Ollama meaning). Ollama: loopback-only
      host, installed-tag list minus cloud aliases, authored RAM/VRAM
      suggestion rows (heuristics, unmeasured), confirm-then-`ollama pull`
      via managed `runner.run` (`OLLAMA_NO_CLOUD=1`, 1800s), preflight
      verify, `num_ctx` ≤4096. OpenAI: `openai_compat.py` client (base-URL
      validation, `/v1` default, https off-loopback, no redirects, generic
      HTTP errors without bodies, `--api-key-env` name-only), off-loopback
      consent before contact, `GET /models` picker or typed ID, wire-format
      adapter (tool-call ids echoed as `tool_call_id`, stringified
      arguments, no Ollama fields). Shared `ChatClient` Protocol at
      `AgentSession`/`_scoped_session`; `run_agent(local_chat=True)`
      untouched.

## Verification actually run

- `uv run --locked pytest -q tests/test_agent_scoped.py
  tests/test_agent_loop.py tests/test_agent_completion.py
  tests/test_agent_bridges.py tests/test_tui_cmd.py
  tests/test_functions.py` → 147 passed (includes the bounded-capture and
  TTY-guard review fixes; no live model involved).
- After the model-free workspace + `local_chat` slice:
  `uv run --locked pytest -q tests/test_tui_cmd.py tests/test_agent_scoped.py
  tests/test_agent.py` → 55 passed — covering the menu→search→inspect flow,
  a guided APT draft whose save prompt was declined (no file, no execution,
  no model), `--chat` delegation with `local_chat=True`, remote-host and
  `:cloud`-model `UsageError`s raised before client construction, and the
  no-redirect transport seam. Targeted `ruff check` on touched files and
  `git diff --check` clean.
- After the provider-flow slice:
  `uv run --locked pytest -q tests/test_tui_cmd.py tests/test_chat_setup.py
  tests/test_agent_scoped.py tests/test_agent.py tests/test_agent_loop.py`
  → 89 passed — covering confirm-gated pull argv/env/timeout,
  decline-runs-nothing, the suggestion table (unknown/8 GB CPU/8 GB+8 GB
  VRAM), a fake-transport OpenAI session through a real scoped AgentSession
  asserting the `call_abc` id echo and scoped-only reach, and off-loopback
  decline before client construction. Targeted `ruff check` and
  `git diff --check` clean.

## Pending / not run

- [ ] A live Ollama session: model adherence to the scoped prompt, JSON
      argument quality, and the real approval UX. Everything above is
      fake-model unit coverage; there is no live evidence yet. Includes the
      `qwen2.5:3b` candidate's real adequacy on the 8 GB CPU baseline (an
      authored default, unmeasured) and a llama.cpp comparison — deferred.
- [ ] The workspace menus on a real tty (questionary paths): tests drive
      them through patched `ui.select`/`ui.text_input` fakes; no human has
      clicked through. `TERM=dumb` plain-mode walk-through likewise unrun.
- [ ] The provider flows live: a real `ollama pull`, a real llama.cpp/
      OpenAI endpoint (e.g. llamactl's `:8080/v1`), bearer auth, and a real
      remote-consent prompt — all fake-transport coverage only so far; the
      suggestion-table numbers are authored heuristics.
- [ ] Real `install_tool` execution end-to-end (depends on the
      install-reliability work's own live-validation gates: sudo policy,
      installers, systemd).
- [ ] `--print` UX on a real tty vs `TERM=dumb` static fallback.
- [ ] Dependency-aware installs (currently refused, D7), `shell-eval`
      function story, extension update/delete (create-only today).
