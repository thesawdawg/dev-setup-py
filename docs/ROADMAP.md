# Roadmap

Proposed next work for `devstuff`, in build order. This is a plan, not a commitment: each item
gets a `docs/specs/<feature>/` directory (see [`specs/README.md`](specs/README.md)) *before*
implementation starts, and that spec — not this file — is where requirements are numbered and
rejected alternatives are recorded.

Last revised: 2026-10-10.

## Theme

`devstuff` is good at *one tool at a time*. Nothing yet answers *"is this machine in the state I
want?"* or *"make this machine look like that one"*. The first four milestones build toward that;
M5 is independent polish that can be slotted in anywhere.

## Order and why

| # | Milestone | Effort | Depends on | Spec directory |
|---|-----------|--------|------------|----------------|
| M1 | `devstuff outdated` | Low | — | `outdated/` |
| M2 | `devstuff profile snapshot` / `diff` | Medium | M1 | `profile/` |
| M3 | `devstuff profile apply` + bundled bundles | Medium–high | M2 | `profile/` (same) |
| M4 | Agent tools for `doctor` / `outdated` | Low | M1 | `agent/` (amend) |
| M5 | Shell completion | Low | — | `completion/` |
| — | git / tmux configurator | Medium | — | `git-config/` (unscheduled) |
| — | `devstuff sandbox <tool>` | Medium | — | `sandbox/` (unscheduled) |

M1 comes first because it is useful on its own and builds the one thing M2 and M3 both need: a
trustworthy "what version is installed, and is it behind?" answer. Shipping it alone also tests
that answer against real machines before anything is built on top of it.

---

## M1 — `devstuff outdated`

A read-only, non-interactive table of installed vs. latest version per tool.

**Spec:** [`specs/outdated/`](specs/outdated/) — **done**, 2026-10-10 (v1; all seven spec milestones).

**Why it is small.** `GenericTool.check_for_update()` already returns an `UpdateStatus`
(`current`, `latest`, `available`) for `npm`, `pip`, `uvx`, `apt` and `git`, and `devstuff update`
already probes every installed tool concurrently for its picker. This milestone is a new command
over an existing probe and an existing collector, not new probing.

**What is actually hard** (revised after measuring the existing path — see the spec's §4).
- **Two thirds of the bundled catalog cannot be checked.** 23 of 35 tools are `bash` installers
  with no update checker. `available is None` is "unknown", never "current", and a *missing*
  checker must be told apart from a *failed* probe — hence five states, not three. Making `bash`
  tools checkable is the real fix and is a follow-up spec, not part of M1.
- **The existing uv probes are redundant and racy.** One run issued 6 `uv tool list` calls where
  2 suffice, however many uv tools are installed: `lru_cache` does not stop concurrent first callers from each computing the value.
  Fix this before building on it.
- **The collector is private to `update_cmd.py`.** It has to move somewhere both commands can
  import, as a pure refactor with a parity test.
- **Probes go through `_probe`**, per the verbosity rules in `CLAUDE.md` — no direct
  `subprocess.run`.

**Done when**
- [ ] `devstuff outdated [keys…]` prints a table; installed tools only by default.
- [ ] Unknown is visibly distinct from up-to-date, and a test pins that for a `script` tool.
- [ ] `--json` for scripting; exit code is 0 regardless of findings (the `run_cmd` lesson: exit
      status is for "could not perform the lookup", not for the answer).
- [ ] `update`'s picker and `outdated` share one code path rather than two copies of the probe loop.

## M2 — `devstuff profile snapshot` and `diff`

**Spec:** [`specs/profile/`](specs/profile/) (draft, 2026-10-10) — covers `snapshot` and `diff`; `apply` (M3) gets its own requirements.

A profile is a YAML file: catalog keys and optional pinned versions. Configurator output is **not** part of it
(several configurators don't round-trip); capturing it is a separate, later decision.

```yaml
version: 1
tools:
  uv: {}
  lazygit: { version: 0.45.0 }
  starship: {}
```

**Design commitments (to be written up in the spec)**
- **A snapshot is measured, not remembered.** It is built by probing `is_installed()` and
  `get_version()` now. There is no separate "installed state" file to drift out of sync — the
  same reason `doctor` and every configurator measure the real binary.
- **Reuse `catalog.py`'s validation style**: unknown fields and unknown keys fail loudly at load
  time, consistent with "invalid catalogs fail loudly".
- **`diff` reports four states**: missing, extra, version drift, and *unverifiable* (a pinned
  `script` tool whose version cannot be probed). The fourth state is the M1 lesson again.
- A profile references catalog keys only. It never embeds install scripts, so a profile file
  cannot become a way to smuggle arbitrary commands past the catalog's validation.

**Done when**
- [ ] `profile snapshot` writes a valid profile; `profile diff` of a fresh snapshot against the
      same machine reports no differences (round-trip test).
- [ ] Keys unknown to the effective catalog are reported, not silently dropped.
- [ ] Spec records the rejected alternative of extending `catalog export` instead, with reasons.

## M3 — `devstuff profile apply` and bundled bundles

Turn a profile into an install plan and run it.

- **Ordering** comes from the existing `requires` graph (including auto-inferred edges such as
  `npm` → `nvm`). Do not write a second dependency resolver; if the existing one is not exposed in
  a reusable form, factor it out first.
- **Plan before action**: reuse the `_plan.py` preview and the task-view progress bar that
  `install`/`update` use, so `apply` shows what it will do and asks.
- **Failure policy** needs a decision in the spec: continue past a failed tool and summarise, or
  stop. Recommendation: continue, skip anything whose `requires` failed, and report at the end —
  a 40-tool apply that dies on tool 3 is worse than one that finishes 39.
- **Pins on `script`/`bash` tools** can only be honoured by a full reinstall. `apply` should say so
  and confirm, matching `update`'s existing behaviour for those types.
- **Bundled bundles** (`@backend`, `@devops`, …) are profile files shipped in the package. They are
  data, so they get a validation test and a CI check that every key they name exists in the
  bundled catalog — the same two-direction enforcement as the CI-matrix test.
- **Configurator state**: restoring starship/lazygit/bat configuration is the differentiator over
  a dotfiles repo, but several configurators do not round-trip (pre-commit and commitizen
  explicitly do not). Scope M3 to tools only and treat configurator capture as a separate,
  later decision recorded in the spec's open questions.

**Done when**
- [ ] `apply` on an empty container (the existing Docker integration harness) reproduces a
      snapshot taken elsewhere.
- [ ] A failing tool does not abort the run; dependents are skipped with a stated reason.
- [ ] Every bundled bundle passes the key-exists test.

## M4 — Agent tools for `doctor` and `outdated`

Expose both as agent tools so the local model can answer "why is my nvm broken?" by running the
checks. Per `CLAUDE.md` this is mostly a YAML edit (`impl: catalog` / `impl: primitive` in
`agent_tools.yaml`); `agent_tools.schema.json` must be updated in the same change if any field is
added.

- `outdated` is read-only and safe to expose unconditionally.
- `doctor --fix` mutates the machine, so the agent gets the **check-only** form. A model
  deciding to apply fixes is a separate question for the spec, not a default.

## M5 — Shell completion

`devstuff completion bash|zsh|fish`, built on Click's native completion support.

- The value is **dynamic completion of catalog keys** for `install`, `remove`, `update` and
  `configure`, which means completing from the *effective* catalog (bundled + user), not a
  generated static list.
- Completion runs on every `<TAB>`: it must not shell out to probes. Keys only, no
  install-state lookups.
- Document the one-line install in README.md alongside the existing shell-integration notes.

---

## Unscheduled candidates

**git / tmux configurator.** Same shape as the seven existing wizards, and the same first step
applies: before writing anything, find what the tool's own validator does *not* check. For git
that means measuring which `git config` keys are silently accepted and ignored.

**`devstuff sandbox <tool>`.** Try an install in a throwaway container before it touches the
host, reusing the Docker integration-test machinery. Not scheduled because it depends on Docker
being present, which the tool cannot assume, and because it overlaps `-vv` plan previews for the
"what will this do?" question.

## Non-goals

- **Not a configuration-management system.** `profile` converges a *developer machine* onto a
  list of catalog tools. It is not Ansible and will not grow conditionals, templating, or
  inventory.
- **No remote profile registry or sync service** in this roadmap. Profiles are files; sharing
  them is the user's git repo.
- **No new install `type`s.** Nothing here adds a mechanism to `GenericTool`; every milestone
  composes what exists.
- **No runtime dependencies.** Same rule the agent follows: devstuff is globally installed, and
  every dependency is paid for by users who never run the new command.

## Risks

| Risk | Affects | Mitigation |
|------|---------|------------|
| Version probes disagree with what a tool actually is (e.g. a shim) | M1–M3 | Probe only through `get_version()`/`check_for_update()`; add live tests against the real binary, per project convention |
| `script`/`bash` tools dominate the catalog and are unverifiable | M1–M3 | Make "unknown" a first-class state everywhere rather than an edge case |
| `apply` partially succeeds and leaves a half-built machine | M3 | Plan preview, continue-and-summarise policy, dependents skipped with reasons |
| Profile format hardens before `apply` has been tried | M2 | Ship `snapshot`/`diff` as `version: 1` but mark the format provisional until M3 lands |
| Scope creep into configurator capture | M3 | Explicit non-goal above; revisit as its own spec |
