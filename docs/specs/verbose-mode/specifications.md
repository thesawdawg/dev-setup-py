# Specification: verbose mode (`-v` / `-vv`)

**Date:** 2026-08-04
**Status:** Implemented (v1)
**Authors:** Sawyer + Claude

---

## 1. Problem Statement & Goals

devstuff is a thin layer over other people's commands. When one of them misbehaves, the
user is looking at devstuff's summary of it rather than the thing itself:

- **Install output was captured and thrown away on success.** `-v` existed on `install`,
  `remove` and `update` as a boolean, but it only reached `generic._run` — which is used
  by the `apt`, `script` and `bash` types. Every `npm`, `uvx`, `git` install called
  `subprocess.run(..., capture_output=True)` directly, so `devstuff install -v <npm tool>`
  streamed nothing at all. The flag silently did nothing for a third of the catalog.
- **A failing function was a black box.** `devstuff run` streams a function's stdout, but
  `run_cmd` flattens every non-zero exit to 1 and a `script`-type function is an opaque
  blob of someone else's shell. There was no way to see *which line* failed.
- **Nothing ever showed the command being run.** The most useful thing a tool can tell you
  when it fails is the exact command it ran, so you can run it yourself.

**Goal:** one process-wide verbosity level, set the same way everywhere, that makes every
command devstuff shells out to visible — and at `-vv`, traceable line by line.

**Non-goals:** a logging framework or log files (`logging`, handlers, rotation); per-command
verbosity; a `--quiet` level below the default; structured/JSON output.

---

## 2. Functional Requirements

### Levels

- **FR-1** Exactly three levels: `0` quiet (default), `1` (`-v`), `2` (`-vv`). Values above
  2 clamp to 2 rather than erroring — `-vvv` is a reasonable thing to type.
- **FR-2** The level is process-wide state in `verbose.py`, not a parameter threaded through
  call signatures. Set once during Click option parsing, read by the subprocess helpers.
- **FR-3** `-v` is accepted **before or after** the subcommand (`devstuff -v install x` and
  `devstuff install -v x`), and on subcommands of groups (`devstuff functions -v enable k`).
  When given in both positions the higher wins; they can never cancel out.
- **FR-4** Every registered command accepts `-v`, applied centrally in `cli._add_verbose_option`
  rather than per-command, so no command can be forgotten. A command already defining
  `--verbose` is left alone.
- **FR-5** `DEVSTUFF_VERBOSE=1|2` (or `true`) sets the starting level, so a function invoked
  from a script can be verbose without the caller threading a flag. An explicit `-v` can only
  raise the level, never lower it.

### What each level shows

- **FR-6** At `-v`: every state-changing command is logged as a pasteable shell line
  (`$ bash -lc 'npm install -g x'`) and its output **streams live** instead of being captured.
- **FR-7** At `-v`: the spinner is replaced by a plain logged line (`verbose.step`). A spinner
  repaints its own line and cannot share a terminal with streaming output.
- **FR-8** At `-vv`, additionally: read-only probes (version checks, `dpkg` queries, install-state
  checks) with their exit code and captured output; the body of any script about to run; the
  resolved parameter values of a function; the sha256 and size of a downloaded install script.
- **FR-9** At `-vv`, script bodies run under `bash -x`, so each expanded command is traced. This
  covers both function scripts (`function_runner`) and tool install/remove scripts (`generic`).
- **FR-10** Probes are silent at `-v`. `devstuff list` alone fires one per tool; at `-v` they
  would bury the actual work. The few *best-effort actions* that share the probe helper
  (`apt-get update`, a git tool's remove command) opt in to `-v` logging via `log_at`.

### Output routing

- **NFR-1** **Every verbose line goes to stderr, never stdout.** `devstuff run <key>` for a
  `register: eval` function prints shell code to stdout for `eval "$(...)"`; a verbose line on
  stdout would be executed in the user's shell. Enforced by the logger having no stdout path
  at all, rather than by per-call-site checks.
- **NFR-2** Verbose lines are not word-wrapped (`soft_wrap`). A wrapped command line cannot be
  pasted back into a shell, which is the main thing users do with one.
- **NFR-3** Commands are rendered with `shlex.join`, so a logged line is a valid shell command.
- **FR-11** An `eval`-mode function's script is **logged but never traced with `set -x`** — it
  is evaluated by the caller's interactive shell, where xtrace would persist after it returns.

### Error reporting

- **FR-12** Quiet mode keeps its existing behaviour: output is captured and the captured stderr
  becomes the `RuntimeError` message on failure. That is the only reason quiet mode can say
  anything useful about a failure at all.
- **FR-13** Verbose mode raises `RuntimeError("exit code N")` instead, since the real output has
  already streamed past and repeating the argv in the message adds nothing.
- **FR-14** At `-v`, a failing `devstuff run` reports the script's real exit code, which is
  otherwise invisible — `run_cmd` always exits 1 regardless.

---

## 3. Out of scope

- Verbose output from the `agent` subsystem's tool calls (it has its own transcript).
- Verbose output from configurators (`configure/`), which already preview what they do.
- Redaction. Verbose output can contain whatever the user passed as a function parameter;
  nothing in devstuff handles secrets, and pretending otherwise would be worse than the
  current honest behaviour.
