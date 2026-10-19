# btop tool + configurator — development plan

One branch (`feat/btop-tool-and-wizard`) and one PR.

## Milestones

- **M-1 Measurements.** Extracted btop 1.3.0 (Ubuntu 24.04 apt), 1.4.6
  (Ubuntu 26.04 apt, GPU build) and 1.4.7 (upstream static musl); recorded the
  rewrite rules, the three validation classes, the per-version/build key sets
  and the capability facts. *Done 2026-10-19* (see `FINDINGS.md` in the task
  record; the load-bearing results are in `model.py`'s docstring and §5 of the
  specification).
- **M-2 Catalog.** `tools.yaml` entry, `test-installs.yml` matrix row, README
  table row (FR-1). *Done 2026-10-19.*
- **M-3 Model + splice.** `model.py` (settings, presets, the
  `save_config_on_exit` rule) and `render.py` (parse/splice/`matches`)
  (FR-2…FR-9). *Done 2026-10-19.*
- **M-4 Detection + validation.** `detect.py` (key set, themes, running procs,
  `from_existing`), `validate.py` (`verify`, `launch`, `rejections`, the pty
  runner) (FR-10…FR-18). *Done 2026-10-19.*
- **M-5 Wizard + capability.** `wizard.py` (report, preset, review loop, save
  path) and `capability.py` (the `cap_perfmon` offer) (FR-19…FR-22).
  *Done 2026-10-19.*
- **M-6 Tests + docs.** `tests/test_configure_btop.py`, this spec directory,
  README and CLAUDE.md. *Done 2026-10-19.*

## Testing

- Unit: `uv run pytest tests/test_configure_btop.py` — needs no btop and no
  network; live tests skip without `btop` on PATH.
- Live: the same file run against each extracted binary via a PATH override
  (`/tmp/btop-measure/{r130,root,up/…}/usr|…/bin`). Live tests cover the
  defaults drift, a clean launch per preset, the can-fail case, the
  kept-silently asymmetry, and the byte-identical stability claim.
- Catalog: `test_ci_matrix_covers_every_builtin_tool` pins the matrix row.

## What was run and what was not

Run on the dev host, 2026-10-19 (btop is *not* installed here; three extracted
binaries stood in via PATH overrides):

- `uv run pytest tests/test_configure_btop.py -q` with no btop on PATH —
  60 passed, 9 skipped (the live tests).
- The same file under `PATH=/tmp/btop-measure/root/usr/bin:$PATH` (1.4.6 apt,
  GPU build), `PATH=/tmp/btop-measure/r130/usr/bin:$PATH` (1.3.0) and
  `PATH=/tmp/btop-measure/up/btop/bin:$PATH` (1.4.7 static) — **69 passed on
  each**, including:
  - the strict defaults-drift test — no drift on any of the three;
  - every preset surviving a real launch with zero rejections;
  - `update_ms = "abc"` and `vim_keys = yes` both reported as rejections (the
    check can fail);
  - `proc_sorting = "bogus"` kept with no rejection (the asymmetry, on 1.4.6
    and 1.4.7; on 1.3.0 the same holds — it also keeps it);
  - a generated file left byte-identical by a real run on all three.
- The wizard driven end-to-end under a pty against the 1.4.6 binary with a
  throwaway HOME: fresh-config save with a preset and one change (live check
  ran), then a second run over the existing file showing the diff and the
  overwrite confirm with a timestamped backup. The capability step printed its
  refusal (the extracted binary is not root-owned) and the grant command —
  the actual confirm could not be reached without a root-owned binary.
  Transcript: `/tmp/btop-measure/wizard-run.txt`.
- `uv run pytest tests/test_catalog.py tests/test_registry.py
  tests/integration/test_tools.py -q` and a full `uv run pytest -q`; `uv run
  ruff check` on the changed files.

Not run:

- **No real `devstuff install btop`** — btop is not installed on the dev host
  and there is no Docker; the weekly `test-installs.yml` canary is the
  install's first run (the CI image's apt has 1.3.0 — the same binary the
  live tests here ran).
- **No sudo**: `grant`/`revoke` were exercised with faked `_run`/`getcap` only;
  the first real grant (and the "apt upgrade drops the capability" re-run) is
  untested against real sudo.
- The wizard's questionary flow was driven through one pty session; the
  per-key menu branches were exercised by unit tests with stubbed prompts.

## Risks

- Ubuntu's apt version lags upstream; a new archive version changes the key
  set — absorbed by run-time detection (SD-7) and by the live drift test if CI
  ever gains a btop.
- The live check's ~5 s cost at save time could read as a hang — the UI states
  it; a hung btop run is bounded by the quit timeout plus SIGKILL.
- An apt upgrade drops `cap_perfmon` — stated in the offer text; nothing
  reinstates it automatically.
