# FMHY picks — development plan

One branch (`feat/fmhy-picks`) and one PR, like #76.

## Milestones

- **M-1 Selection documented.** This directory. *Done 2026-10-13.*
- **M-2 Catalog.** The five tools (FR-1…FR-5) and five links (FR-10), CI matrix entries and README
  rows (FR-6).
- **M-3 fzf script tests.** Run the shipped fzf install/remove scripts in bash against a temporary
  `HOME` with fake `sudo`/`apt-get`/`fzf`, in `tests/test_catalog_scripts.py` (FR-4a…FR-4c).
- **M-4 lazygit offer.** The delta install offer in `configure/lazygit/wizard.py` (FR-7…FR-9),
  with tests, and an update to `docs/specs/lazygit-config/`.

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

To be filled in when the work lands. Expected gaps: no real install of the five tools on the dev
host (none is installed and the host has no Docker), so the weekly `test-installs.yml` canary is
their first real run.

## Risks

- `opencode-ai`'s `postinstall` downloads a platform binary; a network or registry change there
  fails the install even though `npm install` itself works. The CI canary catches it.
- fzf's fallback path is not exercised by the CI container (D-3).
