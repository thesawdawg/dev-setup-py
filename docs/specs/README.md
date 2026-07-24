# Specs

Design documents for features and significant changes, one directory per feature.

```
docs/specs/
└── <feature>/
    ├── specifications.md    # what it does: numbered, testable requirements
    ├── stack-decisions.md   # what we chose and what we rejected, with reasons
    └── development-plan.md  # milestones, testing strategy, risks
```

## Why these exist

Git history records *what changed*. These record *what was decided and why* — including the
options that were rejected, which is the part that gets re-litigated eighteen months later when
nobody remembers why the obvious approach wasn't taken.

They are most useful when they capture:

- **Rejected alternatives with their reasons.** "We didn't use the official Ollama package
  because it pulls httpx + pydantic into every install" is worth more than the decision alone.
- **Findings that changed the design.** A spec that still describes the original guess is worse
  than no spec. When reality contradicts a requirement, update it and say when and why.
- **Explicit non-goals.** Scope creep is easiest to resist against a written out-of-scope list.

## Conventions

- **Keep them current, or delete them.** A stale spec is actively misleading. If a document no
  longer matches the code, fix it in the same PR that broke it.
- **Mark resolved questions with a date and the answer**, rather than deleting the row — the fact
  that something was once open is information.
- **Requirements are numbered and testable** (FR-1, NFR-1) so tests and commits can cite them.
- **Distinguish advisory behaviour from enforced behaviour.** Where a spec describes a security
  property, say which mechanism is the control and which is merely a warning.

## Existing specs

| Feature | Status |
|---------|--------|
| [`agent/`](agent/) — `devstuff agent`, the local-model agent | Complete (v1) |
