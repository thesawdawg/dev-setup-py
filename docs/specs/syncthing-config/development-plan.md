# Development Plan: `devstuff configure syncthing`

**Date:** 2026-10-12
**Status:** Milestones 1–4 complete; first real `devstuff install syncthing` and a live
wizard run against a real service are still pending (see "What was run and what was not").

---

## Milestones

| # | Milestone | Deliverable | Done when |
|---|-----------|-------------|-----------|
| 1 | Catalog entry | `syncthing` in `tools.yaml` (`type: bash`, official v2 apt repo, user service), CI matrix + README rows | `devstuff list` shows it; `test_ci_matrix_covers_every_builtin_tool` passes |
| 2 | Model | `configure/syncthing/model.py` — device-ID Luhn, `paths_overlap`, `Request`, `plan`, `build_*`, `verify` | Every refusal branch and both real device IDs unit-tested |
| 3 | API + detect | `configure/syncthing/{api,detect}.py` — config.xml discovery, urllib `Client`, probes | `discover` fixtures cover loopback/wildcard/tls/unix/non-loopback |
| 4 | Wizard + registry | `configure/syncthing/wizard.py`, `CONFIGURATORS` entry | Happy-path, verify-failure and refusal tests pass; `--list`/`--path` work |
| 5 | Docs | this spec, CLAUDE.md, README | Spec matches the code |

## Testing Strategy

**`tests/test_configure_syncthing.py` — 38 tests, unit by default:**

- **Device IDs:** both real syncthing-generated IDs validate; a last-char flip and a
  single data-char substitution are rejected; lowercase/dashless forms normalise;
  wrong length and bad charset (`0`, `1`, `8`) each get their own reason. The Luhn
  implementation is pinned against the real IDs, not a recollection.
- **`plan()`:** every refusal branch (own ID, path conflict, type conflict, overlap)
  plus create/share/none and the existing-device-is-untouched case.
- **`verify()`:** each silent failure it exists to catch — the dropped folder-device
  entry, the coerced type, the absent device — with config dicts that really exhibit
  the behaviour.
- **`discover()`:** config.xml fixtures for each address shape; `Endpoint.__repr__`
  proven not to contain the key.
- **Wizard:** happy path against a fake `Client` (asserts device-before-folder POST
  order, that the local ID is printed for the server side, and that the API key never
  reaches captured output); verify-failure returns `None`; `--output` refused;
  ID re-prompts; linger-off and service-inactive branches run the right commands.

**Against the real binary (skipped without `syncthing`):**

- `test_pairing_two_real_instances` generates two throwaway instances under a tmp
  `--home`, pairs A→B through the real plan/build/apply/verify path with a folder ID
  containing `/`, and asserts B's `cluster/pending/devices` lists A — the
  "server-side approval still required" behaviour, end to end.
- `test_the_silent_drop_and_type_coercion_the_wizard_guards` asserts the two silent
  behaviours the whole write-order + verify design depends on, so a future syncthing
  that starts *rejecting* them is caught here.
- Both instances are killed in the fixture finalizer via a `pkill` on the unique
  `--home` path — `serve` detaches its worker into its own session, so killing the
  spawned process leaves it behind. Nothing touches `~/.local/state/syncthing` or the
  user's running service.

## What was run and what was not

**Run:**

- `uv run pytest tests/test_configure_syncthing.py` — 38 passed, including both
  real-daemon tests against syncthing v2.1.6.
- The Luhn check against the two real device IDs, and the rejection of the
  flipped-char ID that real syncthing also rejects (400).
- `uv run devstuff configure --list`, `configure syncthing --path`, `devstuff list`
  — syncthing appears in all three.
- `syncthing generate`/`serve --help` against the installed v2.1.6 for the real flag
  names.

**Not run:**

- `devstuff install syncthing` against a clean machine — the install script follows
  the `gh` pattern but has not had its first real run; the CI matrix entry will
  exercise it in a container (where the no-user-session branch is the expected path).
- The interactive wizard under a real pty against the user's real service — the host
  this was developed on has a live syncthing config that must not be mutated; the
  wizard path was exercised with a fake `Client` and monkeypatched prompts instead.
- `sudo loginctl enable-linger` — the command is asserted in a unit test, not
  executed.

## Risks

| Risk | Mitigation |
|------|------------|
| **The API returns 200 for a write it silently mangles** (dropped device entry, coerced type). | Write order (device before folder) plus `verify()` on a re-read config; problems are errors (FR-18/19). |
| **The API key leaks** into output, errors or `-v` logs. | Header-only auth, `ProxyHandler({})`, `Endpoint.__repr__` redaction, and a test asserting the key is absent from captured wizard output (NFR-4). |
| **A folder ID with a `/` needs the collection POST.** | All writes are POST; the live test's folder ID contains a slash so a PUT regression is caught (SD-2). |
| **Ubuntu's archive version gets installed instead of v2.** | The entry uses the official repo by name (`stable-v2`); no pin file needed today — revisit if Ubuntu ever ships a higher-versioned build (SD-3). |
| **The user runs the wizard as root.** | Refused up front — there is no root-owned user service to manage (FR-13). |

## Not built

- `sendonly`/`receiveencrypted` folder modes, multiple folders per run, introducer or
  auto-accept options.
- Server-side approval automation (Open Question 3) — printed as instructions instead.
- The `syncthing@<user>` system unit (SD-5).
- Removing the apt repo, config or linger on `remove` (SD-6).
