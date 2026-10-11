# Specification: `devstuff profile apply`

**Date:** 2026-10-10
**Status:** Approved 2026-10-10 (all open questions resolved as recommended); not yet implemented — see development-plan.md
**Authors:** Sawyer + Claude
**Roadmap:** [M3](../../ROADMAP.md)
**Builds on:** [`profile/`](../profile/) (the file format, the seven-state classifier, `machine_facts`)
and [`outdated/`](../outdated/) (the honesty rule: "could not tell" is never "fine").

---

## 1. Problem Statement & Goals

`snapshot` writes what a machine has and `diff` says how a machine differs from that file. Nothing
yet acts on the difference. Reproducing a machine is still "read the diff, then type forty
`devstuff install` commands in the right order".

**Goal:** `devstuff profile apply PROFILE` makes this machine match a profile: it installs what is
missing (with its dependencies, in order), moves pinned tools to their pinned version, and then
**re-checks the machine and reports what actually matches** — so the exit status is a measurement,
not an assumption that the installers worked.

**Non-goals** (reasons in `stack-decisions.md`):
- **No removal.** `apply` never uninstalls. A tool the profile doesn't name (`extra`) is left
  alone; `diff` already lists them. A command that deletes software because a file omitted it is
  a different, much more dangerous feature.
- **No upgrading of unpinned tools.** `uv: {}` means "present, any version". An installed tool
  with no pin is never touched.
- **No tool definitions in a profile** (unchanged from `profile` SD-2). `apply` can only run what
  *this machine's catalog* already defines — so applying a stranger's profile cannot run a script
  the catalog doesn't contain.
- **No configurator state** (starship, lazygit, bat…). Several don't round-trip; separate spec.
- **No version ordering** — equality only, as in `outdated` and `diff`.
- **No parallel installs** (SD-5).
- **Not configuration management**: no conditionals, templating, or per-host variables.

---

## 2. Functional Requirements

### The plan

- **FR-1** `devstuff profile apply PROFILE` loads the file with `profile.load`. A profile that
  can't be read or isn't valid exits **2** before anything is probed or run, exactly as `diff` does.
- **FR-2** The plan is derived from **the same classifier `diff` uses** (`profile.compare` over
  `snapshot.machine_facts`), never a second comparison. Each profile entry maps to one outcome:

  | `diff` state | `apply` does |
  |---|---|
  | `ok` | nothing |
  | `missing` | install it (FR-6) |
  | `drift` | move it to the pinned version (FR-7) |
  | `unverifiable` | nothing — the installed version can't be read, so a move can't be justified; reported |
  | `unpinnable` | nothing — the pin can't be honoured; reported |
  | `unknown-key` | nothing — not in this machine's catalog; reported |
  | `extra` | nothing (non-goal: no removal) |

  A diff row that is `missing` **and** carries a pin on an unpinnable install type
  (`git`/`script`/`bash`, multi-package `apt`) is **skipped with that reason** and not installed
  at "latest" (OQ-3) — the profile asked for a specific version and `apply` cannot deliver it.
- **FR-3** Dependencies are added to the plan. For every tool to be installed, the tools in its
  transitive `requires` closure that are **not installed** are planned first, using
  `registry.missing_requires` (no second resolver). A dependency the profile doesn't name is
  included and labelled with what needs it (OQ-2). A dependency named by two tools appears once.
  The resulting order is deepest-first: every tool is planned after everything it requires (§4 F-1
  shows why concatenating per-tool closures and keeping the first occurrence is a valid order).
- **FR-4** The plan is **shown before anything runs**: one row per step with the tool, the action
  (`install`, `install pinned`, `update to pin`), the version it will be at when pinned, and — for
  an installed dependency step — "needed by X". Entries that won't be acted on appear below it with
  their reason, so the reader sees the whole picture, not just the part that will run.
- **FR-5** Steps whose installer may ask for a password (`apt`, and any script containing `sudo`)
  are marked in the plan. This is **advisory** — a heuristic over the script text, not a control
  — and the comment saying so lives next to the code (project convention).

### Acting

- **FR-6** Installing a missing tool: with no pin, `tool.install()`. **With a pin, one step:
  `tool.update(version=pin)`** — the same command the installer would run, with a version in it
  (F-3). There is no install-then-update, so there is no throw-away "latest" install and, for
  `npm`/`pip`/`uvx`, no downgrade.
- **FR-7** Moving a drifted tool: `tool.update(version=pin)`.
- **FR-8** **A pin `apt` cannot satisfy fails, honestly.** `apply` does not add `--allow-downgrades`
  or otherwise work around apt (F-2): apt's own error reaches the user and the entry reads as a
  failure. `apply` does not pre-judge it, because whether an older version is installable depends
  on dependency lockstep that only apt can resolve.
- **FR-9** **Failure policy** (OQ-1): a step that fails does not stop the run. Every step whose
  dependency chain includes a failed (or skipped) step is **skipped with a stated reason** — "needs
  `nvm`, which failed" — rather than attempted. The run continues and ends with a summary.
- **FR-10** `apply` is **non-interactive after the one confirmation**. It does not call
  `install_cmd._install_one` as it stands: that function prints a section header, checks
  `is_installed`, and ends by offering the tool's setup wizard (`offer_after_install`) — a prompt
  in the middle of an unattended run (F-6). `apply` has its own step runner, `apply_exec.perform`, which does only the work and reports how it
  went; `install_cmd` is **not** modified (SD-10, decided 2026-10-11 while building A2).
- **FR-11** Steps run **serially, in plan order**, each under the existing verbosity machinery
  (`verbose.step`, `-v`/`-vv`) so nothing here is a second way to run a subprocess.

### Confirming

- **FR-12** After printing the plan, `apply` asks for confirmation (OQ-5). `--yes` skips the
  question. With **no TTY and no `--yes` it refuses**, exit 2, saying so — it must never hang on a
  prompt nobody can answer, and must never proceed unattended on its own. *(Refined 2026-10-11:
  the refusal applies only when there is something to confirm; with an empty plan FR-14 already
  says nothing is asked, so there is nothing to refuse.)*
- **FR-13** `--dry-run` prints the plan and exits 0 without running or asking anything. It is the
  same text a real run shows before the question, so what is previewed is what runs.
- **FR-14** When the plan has nothing to do (everything is `ok` or unactionable), `apply` says so
  and still prints the unactionable list; it does not ask a question about nothing.

### Reporting

- **FR-15** After the last step, **`apply` re-reads the machine and compares again** (`machine_facts`
  + `compare`, with every cached probe discarded first — F-4). The summary is derived from *that*
  comparison, not from the steps' own return values: "14 of 16 entries now match".
- **FR-16** The summary names, with a reason each: the steps that **failed**, the steps **skipped
  because a dependency failed**, and the entries **left unactionable** (FR-2). A pinned step that
  succeeded but whose version can't be read back is reported as **installed, version unverified** —
  not as a failure and not as `ok` (F-5). A step that succeeded but whose tool is **still not
  detected** says so and names PATH as the first place to look (F-8) — a bare `missing` under a ✔
  is baffling.
- **FR-17** **Exit status** is the final comparison's, restricted to the profile's own keys:
  **0** if every entry in the profile now reads `ok`; **1** if any does not; **2** for a profile
  that can't be loaded or a refusal to run unattended (FR-12). `extra` rows never affect it.
  A dry run exits 0 whatever the plan holds (FR-13).
- **FR-18** `--json` writes one JSON document to stdout and nothing else: `{plan, run, rows}` — the
  plan, the per-step results (`null` for `--dry-run`), and the rows (same `to_json` shape as
  `diff`). Everything human goes to stderr or is suppressed, so the output parses (NFR-1). Installers
  print through `ui` and, under `-v`, stream a subprocess straight to the inherited stdout where no
  redirect reaches it, so under `--json` stdout is pointed at stderr and **`-v` has no effect**. `--json` implies it will not prompt: it requires
  `--yes` or `--dry-run` (exit 2 otherwise).
- **FR-19** A `^C` between steps stops the run cleanly: the steps already done stay done, the
  summary of what was and wasn't applied is still printed, and the exit status is non-zero. A
  step interrupted mid-run is reported as such, not as success.

### Bundled bundles

- **FR-20** `devstuff profile apply @name` (and `diff @name`) resolves `name` to a profile shipped
  inside the package — `@backend`, `@devops`, … — loaded through the same strict loader as any
  file. A bundle is data, so two tests guard it (OQ-6): it loads cleanly, and **every key it names
  exists in the bundled catalog** (the two-direction enforcement the CI matrix has: a stale name in
  either place fails the suite, not a user's machine).
- **FR-21** An unknown bundle name is exit 2 and lists the bundles that exist.

### Structure

- **FR-22** The planner is a **pure function**: `(rows, tools, dependency lookup) → Plan`, no
  subprocess and no I/O, so the rules in FR-2/3/9 are exhaustively testable without installing
  anything. The executor and the command are the only parts that act.
- **FR-23** The command lives in `commands/profile_cmd.py` as `profile apply`, registered under the
  existing group, and is added to the help table.

---

## 3. Non-Functional Requirements

- **NFR-1** `--json` stdout is pure JSON; progress, prompts and notes go to stderr.
- **NFR-2** Planning is **local only** — no network call (it reuses `machine_facts`). Only a step
  that installs touches the network, because installing does.
- **NFR-3** `apply` changes nothing until the confirmation (or `--yes`); a refused or aborted run
  leaves the machine exactly as it was.
- **NFR-4** **Idempotent.** Running `apply` twice in a row: the second run's plan is empty. This is
  the test that catches every "reported success but changed nothing" bug, and it is also run live.
- **NFR-5** Every state-changing step's outcome is judged by a **fresh** read of the machine, never
  by a cache populated before it ran (F-4).
- **NFR-6** No new runtime dependency.

---

## 4. Findings from measurement

Per the project convention (*measure the tool, don't recall it*). Date: 2026-10-10.

| # | Finding | Consequence |
|---|---------|-------------|
| F-1 | The bundled catalog (35 tools: 23 `bash`, 7 `uvx`, 3 `npm`, 2 `apt`) has 12 tools with `requires`, only three distinct targets (`uv` ×7, `nvm` ×4, `ansible` ×1), **maximum depth 3, no cycles, no dangling references**. `missing_requires` returns one tool's missing closure deepest-first. Concatenating several tools' closures and keeping each key's *first* occurrence stays a valid order: a key's own dependencies were emitted before it the first time it appeared. | FR-3. Ordering needs no new resolver — but the *union* across the plan does need explicit deduplication, and a test over a shared dependency. |
| F-2 | **An `apt` pin older than apt's candidate fails, with or without `--allow-downgrades`.** Simulated (`apt-get -s`) on `curl` with an older version in the base pocket: `curl : Depends: libcurl4t64 (= 8.18.0-1ubuntu2) but 8.18.0-1ubuntu2.7 is to be installed` — the package's libraries are version-locked to it. A *fresh* install of an older `jq` fails the same way. A pin **equal to the candidate** resolves (`Inst jq`) and a pin equal to the installed version is a no-op. | FR-8: a pin is honourable only when apt itself can resolve it. Adding `--allow-downgrades` would not help and would only make the failure message less clear. Both bundled `apt` tools (`reptyr`, `java`) have a single available version, so this is a custom-catalog concern. |
| F-3 | **`update(version=pin)` on a tool that is not installed is a working one-step pinned install.** Measured in an isolated `UV_TOOL_DIR`: a `uvx` tool absent beforehand ended at `0.0.0.1`, the *older* of the package's two releases, with no install first. The `npm` command differs from the installer only by `@<version>` (`npm install -g name` vs `npm install -g name@ver`), and `apt` is `apt-get install pkg=ver` either way; `GenericTool.update` itself does not require the tool to be installed (only `update_cmd` checks). | FR-6 / SD-1: no install-then-update, and no change to the installers — the question `profile` §6 left open (teach `install()` a version?) is answered *no*. |
| F-4 | **The uv probe memo (`_once`) goes stale across a state change.** Same measurement: `installed_version()` was `''` before the install, `''` *again after it succeeded*, and `'0.0.0.1'` only after `_uv_tool_versions.cache_clear()`. | FR-15 / NFR-5: the post-run comparison must drop every cached probe first. A naive "verify after install" would report a correct pin as unverifiable. Needs a test that fails without the clear. |
| F-5 | `installed_version()` reads uv's list through `shutil.which("uv")`, so it is `''` whenever `uv` isn't on the *process's* PATH — while `is_installed()` can still be `True` (its `check_cmd` runs in a login shell). Measured with `~/.local/bin` removed from PATH: `hey-dave` and `ipython` both `is_installed=True`, `installed_version=''`. A machine that has just installed `uv` in this very run, in a shell that hasn't re-read its profile, is exactly this case. | FR-16: "installed, version unverified" is a real, distinct outcome of a successful pinned step, and must not be collapsed into failure or into `ok`. |
| F-6 | `install_cmd._install_one` mixes four jobs: print a section, short-circuit on `is_installed`, check `missing_requires`, install, then `offer_after_install` (an interactive wizard prompt). `update_cmd._update_one` prompts to confirm for `bash`/`script` types. | FR-10: neither can be called from an unattended loop. `apply` gets its own small runner rather than a shared core (SD-10). |
| F-7 | There is **no plan-preview helper on `master`**. `commands/_plan.py` exists only on the unmerged UI branch. | FR-4 builds its own table now, from `ui.table()`; adopt the shared helper if and when that branch lands (recorded so nobody builds a third). |
| F-8 | **Seen live (2026-10-11), A4.** `is_installed()` for a `uvx` tool is `shutil.which(pip_name)`, so a tool installed into a bin directory that is not on the *process's* PATH reads `missing` immediately after a successful install. Reproduced in an isolated `UV_TOOL_BIN_DIR`: `uv tool list` showed `pycowsay v0.0.0.1`, `which pycowsay` found nothing, and both `apply` and an independent `diff` said `missing`; with the directory on PATH both said `ok` and the plan dropped the step. This is the common case on a fresh machine before the shell has re-read its profile. | FR-16: the verdict stays honest (it is the same test `diff` would give), but a `missing` right under a ✔ gets an explanatory line pointing at PATH. Not worked around — second-guessing `is_installed()` here would make `apply` and `diff` disagree. |

---

## 5. Open questions (all resolved)

| # | Question | Recommendation | Status |
|---|----------|----------------|--------|
| OQ-1 | When a step fails, stop or continue? | **Continue**, skip anything that depended on it with the reason, and summarise (FR-9). A forty-tool apply that dies on tool 3 leaves a half-built machine and no report; one that finishes thirty-nine says exactly what is left. | **Resolved 2026-10-10: approved as recommended** |
| OQ-2 | A tool needs a dependency the profile doesn't name (an `npm` tool without `nvm`). Install it, or refuse? | **Install it, labelled "needed by X"** (FR-3). A hand-written profile naming `claude-code` means a working `claude-code`; refusing makes the user hunt down a prerequisite the catalog already knows about. Snapshots include dependencies anyway, so this only affects hand-written files. | **Resolved 2026-10-10: approved as recommended** |
| OQ-3 | A *missing* tool pinned on an unpinnable type: skip, or install latest anyway? | **Skip with the reason** (FR-2). "Install latest" is the silent substitution this whole feature exists to avoid. `snapshot --versions` never writes a pin for those types, so this arises only in hand-written files, where the fix is one deleted line. | **Resolved 2026-10-10: approved as recommended** |
| OQ-4 | Does `apply` fix `drift` (installed at a different version than the pin), or only install what's missing? | **Fix it by default** — the profile said which version, and a plan that is confirmed first shows both versions side by side. Versions can't be ordered (decision carried from `outdated`), so the plan cannot label something a "downgrade"; it shows `1.2.0 → 1.0.0` and lets the reader judge. A `--missing-only` flag leaves drift alone for people who want only the first half. | **Resolved 2026-10-10: approved as recommended** |
| OQ-5 | Confirmation model. | **Plan, then confirm; `--yes` to skip; refuse without a TTY unless `--yes` or `--dry-run`** (FR-12/13). It mirrors every other state-changing command in the tool, and "never hang, never run unattended by accident" is the one property CI use needs. | **Resolved 2026-10-10: approved as recommended** |
| OQ-6 | Ship the bundled bundles (`@backend`…) in this spec, or later? | **In this spec but as the last milestone**, after `apply` works on a file — they are data plus two tests plus a resolver, and nothing before them depends on them. Which bundles, and which tools in each, is a content decision to make with you at that point, not now. Packaging (do the YAML files ship in the wheel?) is measured then. | **Resolved 2026-10-10: approved as recommended** |
| OQ-7 | Should `apply` also remove `extra` tools (`--prune`)? | **No, not in v1.** It is the one operation here that can't be undone by running `apply` again. If wanted it is its own flag, its own confirmation, and its own spec. | **Resolved 2026-10-10: approved as recommended** |
| OQ-8 | A sudo password prompt arrives mid-run for `apt` and some scripts. Prime it up front with `sudo -v`? | **No.** The credential timeout (commonly 15 minutes) is shorter than a long run, so priming gives false comfort; FR-5 marks the steps instead, and the user can run `sudo -v` themselves first. | **Resolved 2026-10-10: approved as recommended** |
