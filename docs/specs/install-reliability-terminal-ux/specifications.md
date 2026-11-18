# Install reliability & terminal UX — specifications

**Status:** Core implemented; validation pending. The lead's integrated gate,
real sudo/Docker on a systemd VM, actual transparent-terminal QA and the TUI
comparison remain open. See the [`development plan`](development-plan.md) for
validation and [`stack decisions`](stack-decisions.md) for implementation
revisions. The evidence below describes the original baseline at `a2a48ca`
(v2.27.0), not the current behavior.

The motivating report: a user watched `devstuff install docker` appear to stop after
some installer output — the last visible line and devstuff version were not supplied,
so this spec does **not** attribute a cause; it specifies the lifecycle, prompt
policy, diagnostics and verification that make the next report diagnosable either
way. In the same request the user asked for a readable modern CLI on
dark/transparent terminals and for an optional TUI to be evaluated.

## 1. Baseline and evidence

PR #81 (merged 2026-11-16, released in 2.27.0) delivered the first reliability round —
finding F-8 in [`plan-and-progress`](../plan-and-progress/specifications.md#4-findings-2026-10-11):
sudo preflight + keepalive, chunked live output with a fixed ~2s post-exit drain,
bounded shell probes, `DEBIAN_FRONTEND`/`stdin=/dev/null` in the no-view path, and
`install.log`. [The PR body](https://github.com/thesawdawg/dev-setup-py/pull/81)
reports manual Docker verification. It is unknown whether the user's report predates
or follows 2.27.0. This spec is the second round.

The evidence below is source-level (verified against the tree at the base commit)
plus the one user report. Where a runtime claim is unconfirmed, the row says so.

| # | Evidence | Weight | Feeds |
|---|----------|--------|-------|
| E-1 | The quiet capture path in [`_run`](../../../src/dev_setup/generic.py#L190-L225) uses `subprocess.run(capture_output=True)`, which waits on *every* inherited pipe descriptor — a detached grandchild holding stdout keeps `communicate` blocked after the primary child exits (the same failure F-8 fixed only for the live path). The verbose branch in the same function runs bare `subprocess.run` and **bypasses the run log** despite the log being presented as covering everything `_run` runs. | P1 | R1, R5 |
| E-2 | [`_apt_refreshed_at` starts at `0.0`](../../../src/dev_setup/generic.py#L1302-L1329), so on a machine where `time.monotonic() < 600` (early boot) the *first* apt refresh reads as fresh and is skipped. Two tests failed in [CI job 133071554370](https://github.com/thesawdawg/dev-setup-py/actions/runs/43798127460/job/133071554370) — `test_apt_update_runs_once_per_fresh_window` and `test_forget_probes_resets_apt_freshness` — consistent with a low clock, though the original CI clock was not measured. [`forget_probes`](../../../src/dev_setup/generic.py#L802-L816) deliberately invalidates freshness after a mutation (a script may add a repo); that must be preserved conservatively — it does not promise a single refresh across a batch. The live path also lacks the plain path's 180s timeout. | P1 | R7, R2 |
| E-3 | [`_sudo_session`](../../../src/dev_setup/generic.py#L278-L331) warns on a failed `sudo -v` preflight but then opens the live view anyway — the failure mode F-8 fixed is only softened, not eliminated. The keepalive's `sudo -n -v` return code is ignored, so credential loss mid-run is silent. `DEBIAN_FRONTEND` set at the top level may be filtered by `sudo`'s environment reset, so it is not a general no-prompt policy. Whether the user's run actually hit a sudo/auth prompt is unconfirmed. | P1 | R3 |
| E-4 | The [`docker` catalog entry](../../../src/dev_setup/tools.yaml#L272-L301) silences its `systemctl` and compose-plugin fallbacks (`2>/dev/null \|\| true`), and [`install()`](../../../src/dev_setup/generic.py#L446-L457) verifies with `get_version()` — a display string, not the entry's own `check_cmd` postcondition. Docker can therefore report success while the promised Compose plugin is missing, and this path does not establish daemon health or current-session access. | P1 | R6 |
| E-5 | Ranked hypotheses for the observed stop, keyed by the last visible marker: (a) upstream package work or a postinst/auth/lock/service wait while the parent script is alive; (b) the later `systemctl` or compose fallback; (c) a verification or setup prompt after the process exits. No specific attribution to the current report — the point is that each is distinguishable only if per-phase state and diagnostics exist. | P1 | R2, R5 |
| E-6 | [`TaskView._render`](../../../src/dev_setup/ui.py#L345-L390) bakes elapsed/idle timestamps into a static `Group`; a `Live` redraw alone does not recompute them, and the idle counter is measured from `_last_feed`, so a freshly started phase can inherit the previous phase's idle age. The same file's [`task_view`](../../../src/dev_setup/ui.py#L438-L457) uses a transient `Live`, so the final frame can vanish, and gray/default-highlight styling is a low-contrast risk on dark or transparent terminals. | P2 | R2, U1, U2 |
| E-7 | [`offer_after_install`](../../../src/dev_setup/commands/configure_cmd.py#L76-L94) is a command-layer, *optional* prompt after install — its output is not an installer freeze and must stay visibly distinct from the catalog operation. | P2 | R6, U2 |
| E-8 | Docker is excluded from the container canary ([`_SKIP`](../../../tests/integration/test_tools.py#L34): needs privileged Docker-in-Docker), so the container canary does not cover this path; reproduction needs a real VM. | P1 | dev plan |

## 2. Reliability requirements

- **R1 — one managed-child lifecycle.** Every state-changing child devstuff spawns —
  install, remove, update, and the apt refresh — runs through a single supervised
  lifecycle in all four render modes: live view, `-v`/`-vv` streaming, explicit plain,
  and non-TTY/JSON. Rendering and verbosity choose *how output is shown*, never the
  timeout or EOF policy. The lifecycle provides: a bounded post-exit drain (reuse the
  existing ~2s window); a non-blocking, bounded diagnostic tail that copes with
  output lacking a trailing newline, CR-overwritten progress lines, and non-UTF-8
  bytes; close-before-return cleanup of the pipe and log on every exit path —
  including renderer error and interrupt; the foreground child is reaped once it
  exits (subject to the R4 unresolved-privileged case, which reports rather than
  claims); and no killing of a detached service a script deliberately leaves
  running once the run has otherwise completed.
- **R2 — honest phase state.** The progress model distinguishes *active + silent*,
  *waiting for declared terminal input*, *draining*, *verifying*, *cancelled*, and
  *failed*. The clock ticks independently of output, and the idle counter resets at
  each phase start (fixes E-6's inherited idle). Inactivity is advisory — a first
  hint at ~30s of silence — never evidence of death: the view may not infer liveness
  or kill a child on silence alone. Deadlines exist only where a bounded operation
  is already defined: hook/download/probe timeouts, and the existing 180s apt-refresh
  timeout applied in **all** modes including the live path. There is no general short
  deadline on a package install body. The view always shows elapsed time, the last
  output line, the log path, and cancel guidance. Phase boundaries and refresh
  cadence belong to the UI; supervision provides output plus a drain/activity
  callback, not a broad typed event enum (D-3).
- **R3 — prompt policy is separate from color and verbosity.** Devstuff-owned
  managed invocations such as `sudo apt-get …` use `sudo -n` on the actual
  command; noninteractive apt settings are applied at the privileged invocation
  rather than relying on an inherited environment `sudo` may filter. Managed
  children (including opaque scripts, whose bodies are not rewritten) get
  disconnected stdin and no controlling tty: closing stdin alone doesn't stop
  a program from opening `/dev/tty`. There is no detached sudo prevalidation or
  keepalive (D-9): `sudo -v` neither proves permission for the command nor tests
  a command-scoped `NOPASSWD` rule, and failure can wrongly reject that rule.
  If a trusted installer needs real input or terminal authentication, run
  `DEVSTUFF_TERMINAL_INPUT=1 devstuff install <tool>` from a real terminal.
  Passthrough gives the installer the terminal without Rich `Live`, with
  reduced capture. `--yes`, verbosity and `DEVSTUFF_PLAIN=1` don't opt in.
  Non-interactive/JSON mode never enables installer prompting, even if its
  output lands on a terminal.
- **R4 — cancellation.** Cancelling restores the terminal to its prior state and
  reaps the child after it exits; for a privileged workload that does not stop,
  devstuff reports that it may still be active rather than claiming cleanup
  succeeded. SIGINT/SIGTERM is delivered to the owned foreground workload via
  a tested Linux process group where appropriate; escalation is bounded and applies
  only to owned unprivileged children — never a broad `pkill`, and never `SIGKILL`
  of a privileged package transaction or a daemon. The result communicates partial
  changes and any unresolved process state; there is no automatic retry or rollback.
  The `profile apply` interruption contract in
  [`apply_exec.py`](../../../src/dev_setup/apply_exec.py), the existing data schema,
  script-parsing fidelity, and the eval/stdout contracts are all preserved. Ctrl-C is
  reported as *cancelled* — distinct from *failed* — and stops the rest of a batch.
- **R5 — diagnostics that survive the run.** Each action produces diagnostics
  correlated by run ID and phase, with exit status and warnings. Managed modes tee
  output consistently; human diagnostics go to stderr and JSON stays strict — nothing
  but the data reaches stdout. Raw command argv, environment dumps and `bash -x`
  traces are **not** persisted by default. Log files and their directory are
  owner-only with bounded retention, and the logger follows no unsafe paths.
  A logging failure never fails the install. Warnings say plainly that run logs and
  raw `-vv` output may contain secrets — the log is not guaranteed secret-free — and
  advise sharing a sanitized excerpt only.
- **R6 — verify the promise, not the version string.** When an entry declares
  `check_cmd`, use that bounded postcondition after the installer, not
  `get_version()`. A negative *or unavailable* postcondition (including timeout)
  is never reported as an unconditional "installed". This applies only to
  explicit postconditions; general installed discovery remains boolean, not a
  new present/absent/unavailable contract. For Docker specifically — without adding a Docker subclass —
  reporting separates the download / upstream-package / Compose-plugin / service /
  optional-configure boundaries; package and service errors are captured rather than
  masked (`2>/dev/null || true` fallbacks surface somewhere). "Required CLI +
  Compose plugin present" is checked separately from "daemon healthy" and
  "current shell's group membership grants access" — via the supported service
  environment, or reported explicitly as *not checked / unavailable*. The work makes
  no unexpected extra system-policy changes. `--yes` continues to confirm
  devstuff's own plan only: it must not silently opt into post-install setup or
  change the behavior of arbitrary custom scripts; confirmed automation is
  respected, and the optional configure handoff (E-7) is unmistakable.
- **R7 — apt freshness.** The freshness memo gets an explicit *uninitialized*
  sentinel (not `0.0`, which collides with low monotonic clocks — E-2) and one
  consistent 180s deadline; only a *successful* refresh is cached. Post-mutation
  invalidation is kept, so a batch can refresh again after a mutation. An apt
  refresh timeout is fatal to the action: the privileged process or its lock
  may still be active, so do not start another apt transaction on that path.

## 3. Terminal-UX requirements

- **U1 — theme.** The terminal emulator's own background is preserved (black or
  translucent as configured) — devstuff never paints a forced background, including
  on selected or disabled picker rows. Palette tokens:

  | Role | Token |
  |---|---|
  | Primary text | `#E5E7EB` |
  | Metadata / secondary | `#A8B3C2` |
  | Selection / primary accent | `#67E8F9` |
  | Success | `#6EE7B7` |
  | Caution | `#FCD34D` |
  | Destructive / error | `#FDA4AF` |

  Meaning is carried by text and icons, never by hue alone. `dim` is not used on
  essential instructions. Text tokens are checked against two representative dark
  composites — `#000000` and `#20252B` — at a 4.5:1 contrast target; no arbitrary
  desktop theme is guaranteed.
- **U2 — CLI hierarchy.** One consistent shape: **plan → confirmation → progress →
  durable result**. The plan shows the command and any sudo needs before they
  matter; progress never fakes a percentage; the result is a clear
  Done / Failed / Cancelled / Skipped with a next action, plus phase, elapsed, tail
  and log path. Layouts are designed at ~60 columns (compact) and ~100 (standard),
  with an ASCII / no-color / no-motion plain fallback and visible keyboard labels.
  `NO_COLOR` controls color; `DEVSTUFF_PLAIN=1` disables `Live`/animation;
  non-TTY and `TERM=dumb` fall back automatically; `-v`/`-vv` keep their current
  semantics; and no app writes status onto JSON or eval stdout. The existing CLI
  remains the default interface.
- **U3 — pickers and purpose labels.** Buttons name intent: primary
  `[Apply]`/`[Install]`, secondary `[Back]`/`[Cancel]`, and a destructive
  `[Remove]` behind a separate confirmation that defaults to cancel. Focus is a
  distinct marker, not just a color; roles are verbal (labels), not positional only.
  Search keys are documented in place; `↑`/`↓`, space and enter work as today plus
  escape or the existing back binding; already-picked values survive navigation; no
  mouse is required.

  Reference mockups (plain-text, ~60/100 columns — illustrative, not pixel-exact):

  Plan and confirmation:

  ```
  Install plan — docker  (Container runtime + compose plugin · via bash)
    1  fetch    Docker installer to a temporary file
    2  run      downloaded script with sudo (upstream packages)
    3  post     usermod -aG docker $USER
    4  service  systemctl enable + start docker
    5  plugin   compose-plugin fallback via apt/dnf/yum (if missing)
    sudo: required
  Install docker? [Y/n]
  ```

  Progress (stage order matches the script; the setup offer is not a phase):

  ```
    Installing docker               ▰▰▰▰▰▰▱▱▱▱  3/4
    ✔ Fetch installer to a temporary file                  12s
    ✔ Run downloaded script with sudo                    1m04s
    ⠸ Enable + start service            (0m38s, output idle 14s)
    ○ Verify CLI + Compose plugin
    last: Setting up docker-ce (5:28.x-1~ubuntu.24.04) ...
    log:  ~/.local/state/devstuff/runs/20261117-2145/install-docker.log
    Ctrl-C cancels — partial changes are reported, not rolled back.
  ```

  Durable results (stay on screen; the optional setup offer is a separate prompt
  after the result, never a progress phase):

  ```
    Installing docker               ✔ Done — 4/4 (2m31s)
    Docker CLI + Compose plugin verified; docker.service active.

    Optional — not part of the install: Set up docker now? [y/N]
  ```

  ```
    Installing docker               ✖ Failed: Verify CLI + Compose (2m51s)
    Docker CLI found; Compose plugin missing; service status not checked.
    Check the error in the run log. No automatic retry.
    log: ~/.local/state/devstuff/runs/20261117-2145/install-docker.log
  ```

  Compact picker (~60 columns):

  ```
  Select tools — 2 picked                        (type to filter)
  ❯ [x] docker        needs sudo · bash
    [x] lazydocker    via github release
    [ ] podman        installed · via apt
    [ ] kubectl       via apt
  [Install 2]  [Back]
  ↑↓ move · space pick · enter go · esc back · type to filter
  ```

## 4. Optional TUI evaluation

- **T1 — `devstuff tui` (candidate name, not a committed CLI contract).** An
  explicitly invoked TUI — never the default — may be *evaluated* for interactive
  tty use only. The first artifact is a read-only inventory/plan prototype plus
  fixture-driven progress; no execution path is wired until the R-series
  reliability gates pass. It must sit on the same plan/runner interfaces as the CLI
  (no duplicate execution logic), handle terminal handoff/restore cleanly, and pass
  the accessibility checks in the development plan. Any future distribution
  would be optional and add nothing to the CLI hot path.

  **Status:** the user-authorized experimental slice now exists —
  `devstuff tui` renders the effective catalog and *nominal* install plans
  through the shared `dev_setup.tui.browse` renderer (also driven by the
  [`dev/tui_preview.py`](../../../dev/tui_preview.py) fixture). It performs no
  installed-state or requirements probes and no subprocess execution, and it
  falls back to a plain inventory below 80x16, on `TERM=dumb`, or under
  `DEVSTUFF_PLAIN=1`. Promotion to any execution role, a real-terminal
  hands-on comparison, and the Textual evaluation all remain pending gates;
  no dependency change was made for it.

  Optional layout sketch (evaluation only):

  ```
  ┌ Tools ──────────┬─ docker ──────────────────────────────────┐
  │ ❯ docker    ins │ plan                                      │
  │   lazydocker    │  ✔ fetch upstream installer               │
  │   podman     ✓  │  ⠸ run get.docker.com (sudo)      0m41s   │
  │   kubectl    ✓  │  ○ enable + start service                 │
  │                 │  ○ verify CLI + Compose                   │
  │                 │ tail: Setting up docker-ce …              │
  ├─────────────────┴───────────────────────────────────────────┤
  │ tab pane · ↑↓/jk move · space pick · enter apply · q quit   │
  └─────────────────────────────────────────────────────────────┘
  narrow (<80 cols): the same sections stack vertically
  ```

## 5. Non-goals

- No default fullscreen/alternate-screen UI, no browser UI, no custom terminal
  engine, no PTY wrapping of every script, and no rewrite of the installer
  architecture (per-tool Python subclasses).
- No blanket `DEBIAN_FRONTEND`-style environment as an enforcement mechanism, no
  global forced timeout on package work, no default auto-retry.
- No change to what `--yes`, `-v`/`-vv`, `--json`, or the eval-mode function
  contract mean (beyond the explicit items in R3/R6/U2).
- No claim about the specific cause of the user's Docker report; the spec makes the
  next occurrence diagnosable instead (E-5).
