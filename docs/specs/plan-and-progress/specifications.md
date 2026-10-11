# Specification: plan preview, live progress, and the guided `add` wizard

**Status:** Implemented. Written **after** the code (2026-10-11), when the work was picked up for
review, so this records what was built and what was measured about it. `stack-decisions.md` is
deliberately absent: the rejected alternatives were never written down by the author, and inventing
reasons would be worse than having none. If you remember why a choice was made, add it there.

## 1. Scope

`install`, `remove` and `update` now show **what will run** before running it, ask once, run with a
live checklist instead of a spinner, and end a multi-package run with a summary. `add` is a stepped
wizard with back-navigation and a review screen. The pickers share one full-width row style.

**Out of scope:** `profile apply` (it has its own plan and summary, and drives the same
`tool.install()` / `tool.update()` underneath — see §4), the `configure` wizards beyond the two
call-site tweaks listed in §3, and any change to what the installers themselves do.

## 2. Requirements

Each is covered by a test in `tests/test_lifecycle_cmds.py`, `tests/test_plan_and_select.py` or
`tests/test_ui.py` unless it says otherwise.

### Plan and confirmation

- **FR-1** Before anything is changed, `install`/`remove`/`update` show the resolved plan — the real
  argv for `npm`/`apt`/`uvx`/`git`, the script body or source URL for `bash`/`script` — and ask once.
  One package gets the full panel; several get one batch table and **one** question for the run.
- **FR-2** Defaults: `install` and `update` default to **yes**; `remove` defaults to **no**.
- **FR-3** `-y` / `--yes` skips the plan *and* every confirmation, on all three commands and in
  their pickers.
- **FR-4** The plan covers only work that will happen. Already-installed tools (`install`) and
  not-installed tools (`remove`, `update`) are reported and filtered out first; `update` points at
  `devstuff install <key>`.
- **FR-5** Declining anything installs, removes or updates nothing, prints `Aborted`, exits 0.

### Requirements, failures, summary

- **FR-6** `install`: a missing `requires` is *offered* ("`X requires Y — install them first?`");
  with `--yes` it is installed first, in order. Declined → that package fails and is not attempted.
  A requirement missing from the catalog fails the package without trying. If a requirement fails,
  its dependent is never attempted.
- **FR-7** A failure does not stop a multi-package run; the rest are attempted. The exit status is
  1 if any failed. A summary table (`ok · failed` counts, one row per package) is printed when there
  is more than one result, and **not** for a single package.
- **FR-8** `remove` warns which catalog tools list the target in `requires` — a warning, never a
  veto — and the picker annotates those rows.
- **FR-9** `update --version V` needs exactly one package. Results distinguish *already up to date*,
  *`old → new`*, *updated to `x`*, and plain *updated* when nothing could be read back.
- **FR-10** After a successful `install`, the tool's setup wizard is offered (if it has one); not
  after a failure.

### Progress view

- **FR-11** While a tool installs/removes/updates, a live region shows the phases as a checklist,
  a progress bar, and the last few lines of real installer output. On success it collapses to the
  section header and one result line; on failure the output stays.
- **FR-12** The view degrades to the plain `verbose.step` line when `-v` is on or the console is not
  a terminal. `_run` then has three modes: feed the view; stream raw (verbose); capture and surface
  the output as the error message on failure.
- **FR-13** A partial line (a `sudo` password prompt, a curl progress bar) is pushed to the tail while
  the process still waits, so a prompt is visible rather than the terminal looking hung.
- **FR-14** Every install type has a `_PLANNERS` entry. Without one the plan is a generic note and
  the bar shows no phases.

### `add` wizard and pickers

- **FR-15** `add` runs as numbered steps (`step i/N`). `Ctrl+B` on a text prompt, or `← Back` on a
  select, moves back and keeps later answers as defaults. Backing out of step 1 aborts.
- **FR-16** A review screen lists exactly what will be saved (falsy answers hidden) and lets any
  field be re-edited; editing the type re-derives which steps apply.
- **FR-17** Picker row titles are plain strings, padded to the row width so the highlight is a bar.
  A styled token-list title never receives the `highlighted` style and would hide the cursor.
  `select(searchable=True)` turns search on and the `j`/`k` keys off (they would be typed as search
  text); a `default` is still matched by *value* after padding.

### Not parsed as markup

- **NFR-1** Text that comes from outside — an installer's error line, a command in a plan — is
  never parsed as Rich markup. Tables take `Text` cells; failure messages are `escape()`d.
- **NFR-2** Nothing here may change `--json` output of other commands: off a terminal the view is
  inert (FR-12).

## 3. Touch points outside the commands

`generic.py` (`plan_items`, `_PLANNERS`, `ui.phase` around every installer phase, `_run`'s three
modes), `registry.dependents()`, the starship wizard (`install_by_key(..., yes=True)` for the font it
has just asked about, and plain-string picker titles), and the four configurator wizards'
`install_by_key` calls.

## 4. Findings (2026-10-11)

| # | Finding | Consequence |
|---|---------|-------------|
| F-1 | **Rich markup crashed and deleted text.** Measured: `ui.error("… [/usr/bin/x] …")` raises `MarkupError`; `[lock]` in `ansible-lint[lock]` was silently deleted from the batch plan table, so a plan did not show the command that would run. `ui.summary` had the same flaw with an installer's error line. `master` already had the `ui.error(f"Failed … {exc}")` pattern, so this was a latent crash on the failure path of a multi-package run. | NFR-1. Fixed for the surfaces this work owns: `batch_plan_panel` and `summary` use `Text`; the three failure messages `escape()`. **Not** fixed: about ten other sites interpolate error text into `ui.error`/`ui.dim` (`configure_cmd`, `doctor_cmd`, `run_cmd`, `skills_cmd`, the commitizen and reptyr wizards, `add_cmd`). |
| F-2 | **`profile apply` is unaffected.** Run in a pty in an isolated `HOME`: a pinned `commitizen` apply printed its own plan and per-step lines with no stray progress-view output and exit 0; with `--json` and stdout to a file, the file was pure valid JSON and the view stayed out of it. | NFR-2 holds. `apply` needs no change. |
| F-3 | **Padding does not break defaults.** `questionary` matched `default=` on `Choice.value`, so a padded title with an unpadded value selects the same row as the old plain strings (checked for `npm`, `apt`, `bash` against questionary's real control). | FR-17. |
| F-4 | **Merging onto 2.13.0 conflicted in two places**, both "master moved while this was in progress": `ui.select` (master added `searchable` for `links`; this added padding) and `update_cmd` (master moved the collector into `updates.py` for `outdated`). Both sides kept. | `_pad_choices` and `searchable` are tested *together* (`test_plan_and_select.py`). |
| F-5 | **A missing requirement asks three questions.** `install pkg` with `dep` missing asks `Install Pkg?`, then `Pkg requires dep — install them first?`, then `Install Dep?` (the requirement's own plan). | Current behaviour, not pinned by a test. A candidate to tighten: the second answer already implies the third. |
| F-6 | **The step counter changes once the type is known** (`step 1/7`, then `2/8`) because the type-specific steps only count after a type is chosen. | Cosmetic; left as is. |
| F-7 | `profile apply`-style "stuck" prompts under a pty harness were the *harness*: `prompt_toolkit` asks the terminal for the cursor position (`ESC[6n`) and waits. Not a product defect. | Recorded because it cost a false alarm: a pty driver for these commands must answer it. |

## 5. Tests

`tests/test_lifecycle_cmds.py` (stateful fakes over the real command layer: every decision in
FR-1–10 plus the markup cases), `tests/test_plan_and_select.py` (`_plan.py`, `_pad_choices`,
`select`/`checkbox`, default matching against questionary's real control), `tests/test_ui.py`
(planners, task view, `_run`, `dependents`, the wizard framework). Mutation-checked: 45 deliberate
breakages of the commands, `_plan.py` and `ui`, all caught; two survivors were a missing test (a
single package must get no summary) and a redundant length condition in `_pad_choices`, now removed.

Verified live (not in CI), in an isolated `HOME`, in a pty: `install` (with and without `-y`, and a
failing installer), `remove` (declined, then `-y`), `install` with confirmation and the setup-wizard
offer, `profile apply` in a terminal and with `--json`, and the `add` wizard including a `Ctrl+B`
back-step and the file it writes.
