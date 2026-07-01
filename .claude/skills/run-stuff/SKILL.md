---
name: run-stuff
description: Run, build, test, and smoke-test the dev-setup CLI; exercise list/catalog/install commands
---

`dev-setup` is a Python CLI that manages a Linux dev environment. It is driven directly via `uv run dev-setup <command>` — no GUI, no server, no browser. The smoke script at `.claude/skills/run-stuff/smoke.sh` is the primary agent harness.

## Prerequisites

- Python 3.11+
- `uv` (installed at `~/.local/bin/uv`)

Install the package in editable mode once:

```bash
uv pip install -e .
```

## Run (agent path)

Run the smoke script from the repo root:

```bash
bash .claude/skills/run-stuff/smoke.sh
```

It exercises `version`, `list` (all variants), `catalog path`, `catalog export`, and `--help`. Exits 0 on success, 1 on any failure.

To exercise a specific command directly:

```bash
uv run dev-setup version
uv run dev-setup list
uv run dev-setup list core
uv run dev-setup list --installed
uv run dev-setup list --available
uv run dev-setup catalog path
uv run dev-setup catalog export /tmp/export.yaml
uv run dev-setup --help
```

Interactive commands (`install`, `remove`, `add`, `delete`) require a TTY and are not smoke-testable without mocking. To test them, run the app directly in a terminal.

## Run (human path)

```bash
./dev-setup list        # bash wrapper — bootstraps .venv on first run
./dev-setup install     # interactive multi-select picker
```

## Test suite

```bash
uv run pytest           # unit tests only (integration tests skipped by default)
uv run pytest -m integration   # real installs — requires sudo + network
```

All 6 unit tests pass in ~0.3s.

## Gotchas

- `dev-setup` is not on `$PATH` directly in the dev environment — `uv run dev-setup` is the reliable invocation path. The `./dev-setup` bash wrapper also works from repo root and bootstraps its own `.venv`.
- `pip` is not available in this environment; `uv pip install` is the substitute.
- Interactive commands (`install`, `add`) open questionary/click prompts that hang in non-TTY shells. Pipe input only if you know the exact prompt sequence.
- `catalog export` writes to a path argument; without an argument it prints to stdout. Check the README for the exact signature.
