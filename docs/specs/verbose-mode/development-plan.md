# Development plan: verbose mode

**Date:** 2026-08-04
**Status:** Complete (v1)

---

## Milestones

| # | Milestone | State |
|---|-----------|-------|
| 1 | `verbose.py`: level, env var, stderr printers, `step`, Click option factory | Done |
| 2 | `generic.py`: `_run` / `_probe` split; route every `subprocess.run` through one of them | Done |
| 3 | `generic.py`: swap spinner-plus-capture call sites (npm/uvx/git) onto `_run` + `verbose.step` | Done |
| 4 | `function_runner.py`: log resolved params, script body, `bash -x` at `-vv` | Done |
| 5 | `cli.py`: apply the option to the group and every command/subcommand recursively | Done |
| 6 | Tests (`tests/test_verbose.py`), README, CLAUDE.md | Done |

## Testing strategy

`tests/test_verbose.py`, 33 tests. The level is process-wide state, so an autouse fixture
resets it before and after every test — a leaked level would make unrelated tests print.

What is actually asserted, beyond the obvious:

- **stdout stays empty at `-vv`.** Two tests: one over every printer in `verbose.py`, one over
  `render_eval_script`. This is the invariant in SD-3 and the only one whose failure would
  execute arbitrary text in a user's shell.
- **`_run` streams rather than captures when verbose** — asserted on the `subprocess.run`
  kwargs, because "did output reach the terminal" is not otherwise observable in-process.
- **`_probe` is silent at `-v` and reports exit code + output at `-vv`**, and never raises.
- **`bash -x` appears at `-vv` and not below**, for both functions and install scripts, plus one
  end-to-end test (`capfd`) that a real function's output and its `+ echo …` trace both arrive.
- **The CLI accepts `-v` before and after the subcommand**, parametrized over both positions and
  both levels — this is the case that broke in development (SD-2).
- **The spinner is not used when verbose** (SD-6), asserted by patching `ui.spinner`.

Manual verification against the real CLI, with a throwaway `bash`-type tool in an isolated
`HOME`: quiet / `-v` / `-vv` / `DEVSTUFF_VERBOSE=1` on both a succeeding and a failing install,
and `--help` on all fourteen commands confirming each advertises `-v`.

## Risks

- **Verbose output can contain secrets** a user passed as a function parameter. Not mitigated in
  v1 and stated in the spec rather than half-solved (spec §3).
- **`bash -x` changes a script's stderr**, so a function whose stderr is parsed by something
  downstream would see the trace. Only reachable at `-vv`, which is an interactive debugging
  flag; `capture=True` (the agent path) also gets the trace, and the agent reads it as text.
- **The env var is read at import time.** Changing `DEVSTUFF_VERBOSE` mid-process has no effect;
  tests call `_from_env()` directly rather than relying on re-import.
