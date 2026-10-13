# FMHY picks — development plan

One branch (`feat/fmhy-picks`) and one PR, like #76.

## Milestones

- **M-1 Selection documented.** This directory. *Done 2026-10-13.*
- **M-2 Catalog.** The five tools (FR-1…FR-5) and five links (FR-10), CI matrix entries and README
  rows (FR-6). *Done 2026-10-13.*
- **M-3 fzf script tests.** Run the shipped fzf install/remove scripts in bash against a temporary
  `HOME` with fake `sudo`/`apt-get`/`fzf`, in `tests/test_catalog_scripts.py` (FR-4a…FR-4c).
  *Done 2026-10-13.*
- **M-4 lazygit offer.** The delta install offer in `configure/lazygit/wizard.py` (FR-7…FR-9),
  with tests, and an update to `docs/specs/lazygit-config/`. *Done 2026-10-13.*

## Testing

- Unit: `uv run pytest`, including the catalog/matrix coverage tests that already require a matrix
  entry for every built-in.
- fzf block: source the written `~/.bashrc` in a fresh `bash -i`-like run for each case — `fzf
  --bash` works; `fzf --bash` fails and the fallback file exists; neither; fzf absent — and
  assert a zero exit and empty stderr. Each test must fail against a deliberately broken block.
- lazygit: drive `_ask_pager` with stubbed `ui`, `shutil.which` and `install_by_key`: offer made
  and accepted, declined, install failed, non-delta pager missing (no offer), delta present (no
  offer).

## What was run and what was not

Run on the dev host, 2026-10-13:

- `uv run pytest tests/test_catalog_scripts.py tests/test_configure_lazygit.py
  tests/test_catalog.py tests/test_links.py -q` — 125 passed, 12 skipped, including
  `test_ci_matrix_covers_every_builtin_tool` (both directions).
- Full `uv run pytest -q` — 2371 passed, 52 skipped (12 are the lazygit live tests, skipped
  because lazygit is not on this host).
- `uv run ruff check .` — clean.
- `bash .claude/skills/run-stuff/smoke.sh` — all 11 checks (after fixing its stale
  `1\.` version expectation; the project is at 2.x).
- `devstuff list` shows all five tools as not installed.
- Teeth checks: the fzf block tests were shown to fail against a naive `eval "$(fzf --bash)"`
  block (stderr leaks in the three non-happy cases); the fzf `check_cmd` marker test was shown
  to fail against the old `check_cmd: fzf` (a binary alone returns 0); and the lazygit gate
  tests 1, 4 and 7 were shown to fail against a stubbed-out `_DeltaGate.check`. All restored.

Not run:

- No real install of the five tools on the dev host (none is installed and the host has no
  Docker, and no sudo/apt/npm installs were run), so the weekly `test-installs.yml` canary is
  their first real run. For fzf, that test's `is_installed()` covers the binary and the
  `~/.bashrc` marker (FR-4d), but not that the key bindings actually load: the CI image's fzf
  0.44 has no `--bash` and the minimized image likely has no fallback file (D-3).
- The interactive lazygit wizard under a pty; the gate was exercised with stubbed prompts.

## Risks

- `opencode-ai`'s `postinstall` downloads a platform binary; a network or registry change there
  fails the install even though `npm install` itself works. The CI canary catches it.
- fzf's fallback path is not exercised by the CI container (D-3).
