# Install reliability & terminal UX — development plan

**Status: Core implemented; validation pending.** The packages below track
implementation separately from evidence and acceptance gates. See
[`specifications.md`](specifications.md) for the requirements and
[`stack-decisions.md`](stack-decisions.md) for the revised interfaces.

## Staging map

```
A ──► B ──► D (generic.py verification wiring) ──► E
      B's output/activity callbacks ──► C (view integration) ──► E
      C's theme/mockup work runs in parallel with B
      F fixture preview is independent; promotion stops at D-8
```

| Pkg | Owns | Depends on |
|-----|------|------------|
| A | `generic.py` (apt freshness), `tests/test_generic.py` | — |
| B | new runner module + its test file, `generic.py` (lifecycle) | A |
| C | `ui.py`, theme tokens, static mockups | B for output/activity wiring |
| D | `tools.yaml`, repro fixture + VM docs | B for `generic.py` verification wiring |
| E | command layer, `configure` offer, docs | B + C + D |
| F | `dev/tui_preview.py` (fixture spike only) | none for static fixtures; D-8 gates any promotion |

## Packages

### A — apt freshness sentinel (P0, blocks B)

- [x] Replace the `0.0` sentinel in `_apt_refreshed_at` with an explicit
      uninitialized value (`src/dev_setup/generic.py`, E-2/R7).
- [ ] Regression tests in `tests/test_generic.py`: a deterministic fake clock
      below `_APT_REFRESH_MIN_S` proves the first refresh runs; rerun the two CI
      failures (`test_apt_update_runs_once_per_fresh_window`,
      `test_forget_probes_resets_apt_freshness`) green.

### B — managed-child runner + diagnostics (P1)

- [x] Route `_run` through [`runner.py`](../../../src/dev_setup/runner.py)
      in managed render modes. Supervise/drain the foreground child and expose
      output plus drain/activity callbacks (D-3); do not add a typed event bus.
- [x] Apply the existing 180s apt-refresh deadline across render modes. A timeout
      is fatal to the action so it cannot launch another apt transaction (R7).
- [x] Isolate managed stdin and controlling tty; devstuff-owned sudo uses
      `sudo -n` on the actual command. No detached prevalidation or keepalive.
      Only explicit `DEVSTUFF_TERMINAL_INPUT=1` on a real terminal outside
      machine mode permits installer input (R3, D-9).
- [x] Wire best-effort private action logs, output capture and phase correlation
      (R5). Passthrough has reduced capture.
- [ ] **Gate:** fake-child suite (below) green in all modes; JSON/eval contracts
      intact.

### C — theme + phase view (P2, parallel with B)

- [x] Central theme tokens per U1 (`ui.py` / theme module); remove forced
      backgrounds and `dim` on essential instructions; picker focus marker +
      verbal labels per U3.
- [x] UI owns phase boundaries and elapsed/idle refresh, resets idle at phase
      start, and keeps a durable final result (E-6). Wire output and the
      drain/activity notification from B.
- [ ] Validate plan/progress/failure/cancelled renders and the compact picker at
      ~60 and ~100 columns on an actual transparent terminal (U1–U3).

### D — Docker reproduction + verification (P1, parallel with B/C)

- [ ] Dedicated reproduction: disposable systemd VM or scratch host + a documented
      fixture; record version/command/distro/mode/last marker/elapsed/log excerpt
      (E-8 — the container canary can't run this).
- [x] `tools.yaml`: surface `systemctl` and Compose fallback diagnostics.
- [x] Use a bounded explicit `check_cmd` postcondition rather than `get_version`
      for install verification; distinguish the CLI + Compose promise from
      daemon state and current-session access. General installed discovery
      remains boolean (R6, D-10).
- [ ] **Gate:** a fixture where the installer succeeds but Compose is missing must
      *not* report an unconditional "installed".

### E — integration & docs (after B + C + D)

- [x] Wire command layer (`install`/`remove`/`update`, `profile apply`), the
      configure offer's unmistakable handoff, and the `--yes`/JSON/cancellation
      semantics across the new runner.
- [x] Update `docs/specs/` status rows and CLAUDE.md touch-points; record
      requirement IDs in handoff notes.
- [ ] **Lead gate:** interface and output/backcompat review; no duplicated
      lifecycle logic; no concurrent same-file edits across packages.

### F — optional TUI discovery (gated, stack-decisions D-8)

- [x] Dev-only [`prompt_toolkit` spike](../../../dev/tui_preview.py) with
      read-only fixtures; exercised independently in a PTY. No execution.
- [ ] Compare Textual with the existing stack; run the same pick/search/review
      task on the CLI and prototype(s), record steps and time, and ask which felt easier.
      Promote only on a clear improvement *and* with the rest of the D-8 gates
      passing; write the verdict in `stack-decisions.md` (with date) either way.
      No promotion, dependency change or CLI command until that decision.

## Working protocol

- One independent worktree per agent: independent packages start from the pinned
  integration baseline (`a2a48ca`); dependent packages branch from the integrated
  tip containing their prerequisites. Short focused changes; **no push**; no
  simultaneous edits to shared files — the lead integrates and reviews.
- Each handoff records: files touched, requirement IDs (R/U/T) covered, commands
  run and their results, and what was **not** run plus risks.

## Automated minimum (what CI/tests must cover)

- The two apt tests with a deterministic fake clock (A).
- One end-to-end fake child whose pipe is held by an orphan grandchild — in live,
  verbose, and noninteractive capture (explicit plain if distinct) — asserting
  bounded drain and no hang (R1).
- Interruption + terminal-restoration test under a pty, no real installers (R4).
- A Docker-shaped failure fixture: successful installer, missing Compose → not
  "installed" (R6).
- A JSON/eval stdout contract test under a forced-tty harness — extend the
  existing apply test if it already forces a tty, otherwise add one; do not
  assume coverage exists.
- One managed actual-command / no-tty test with fakes — no real sudo (R3).
- No snapshot-fixture proliferation — fixtures stay minimal and shared.

## User QA (needs a human on a real machine)

On a disposable systemd-enabled VM snapshot — not a daily driver, and not the
container canary, which skips docker:

- Restore the VM to a fresh snapshot for each `devstuff install docker` attempt.
  With uncached sudo that requires a password, expect a managed run to fail
  without a hidden prompt. Compare with cached credentials using the VM's
  actual sudo policy (a tty-scoped cache might not carry into the child), and
  with policy that allows the actual command. Where terminal auth is needed,
  repeat as `DEVSTUFF_TERMINAL_INPUT=1 devstuff install docker` and check that
  the installer visibly owns the tty and capture is reduced. Capture devstuff
  version, command, distro, mode, the last visible marker, elapsed time and a
  sanitized log excerpt.
- Compare default vs `-v` from equivalent VM snapshots; expected: no hidden
  prompt, and a bounded finish *after the installer exits* — not a blanket
  runtime bound on the install itself.
- Afterwards observe `docker --version`, `docker compose version`,
  `systemctl is-active docker` and `docker info` in the intended session.
  Check that the result does not infer daemon health or shell access from
  CLI + Compose verification. Evaluate the optional configure offer separately.
- Ctrl-C against a safe fake fixture — never against a live dpkg transaction.
- Boot-clock check: reproduce the low-monotonic case with a fixture (A).
- Terminal UX: a real black/transparent terminal at ~60 and ~100 columns;
  arrow/space/enter/back through the picker, checking contrast, visible focus
  and a readable final result. Try `NO_COLOR=1`, `DEVSTUFF_PLAIN=1`, redirected
  streams, a JSON parse check and eval-mode output with no chatter.
- Fixture preview: run `.venv/bin/python -B dev/tui_preview.py` from the repo
  root. Move with Up/Down, mark with Space, use `/` to filter, and use Enter or
  Esc to return to the list. Verify that hidden picks remain in the plan, Enter
  toggles plan/detail from the list, and `q` exits cleanly. Below 80 columns or
  16 rows expect a plain preview. Give feedback on which of the CLI and preview
  is easier for the same pick/search/review task; this is not installer QA.
- Report back which last marker or contrast/keyboard issue is seen — that feeds
  the next iteration, per E-5.

## What ran vs what is pending

Recorded evidence:

- On the proposal baseline `a2a48ca`, the two focused apt freshness tests passed:
  `test_apt_update_runs_once_per_fresh_window` and
  `test_forget_probes_resets_apt_freshness`. The CI job 133071554370 failures did
  not reproduce on that host; the CI monotonic clock was not measured.
- The dev-only fixture spike was exercised independently under a PTY. That is
  not validation of the managed runner, installer passthrough or real terminal
  accessibility.
- Integrated gate on the shared branch (after the A+B runner/sudo/check_cmd
  integration): `ruff check .` clean, `git diff --check` clean, and
  `pytest -q` → **2496 passed, 15 failed, 57 skipped**. The 15 failures were
  interface/fixture regressions in the integrated change: `ui.table` had been
  switched to an ASCII frame whenever output was non-tty, which broke the
  row-parsing assertions in `tests/test_managers.py` (1),
  `tests/test_outdated_cmd.py` (5) and `tests/test_profile_diff_cmd.py` (9);
  and the `install` picker's new non-TTY refusal needed a faked interactive
  seam in the managers picker test. Compatibility repairs applied: `table()`
  now uses ASCII only on an explicit plain/dumb request (redirected streams
  keep the existing rounded frame — no live motion either way), and the one
  fixture was updated. The three affected suites were rerun after the fix:
  `tests/test_managers.py tests/test_outdated_cmd.py
  tests/test_profile_diff_cmd.py` → **106 passed**. No post-fix full-suite
  rerun is claimed here.

Pending: real sudo, Docker, and a systemd VM walkthrough have not been run for
this round. Actual transparent-terminal QA, the Textual comparison and the
hands-on TUI comparison remain pending.
