# Stack Decisions: Proxmox management functions

**Date:** 2026-09-16
**Context:** The eighth configurator, and the first one that configures **devstuff's own
access to something else** rather than a local tool's config file. SD-1 and SD-2 in
`docs/specs/starship-config/` (configurators are Python, dispatched from a dict) apply
unchanged. Everything below is about the parts that are genuinely new: a wizard with no
tool to configure, a shell/Python boundary, and a secret.

---

## SD-1 — Functions are the deliverable; the wizard exists to feed them

**Decision: the user-facing surface is fourteen `devstuff run pve-*` functions. The
configurator exists only to store connection profiles for them.**

The request was "helpful scripts to manage a Proxmox instance … should better guide a user
to the correct commands". Scripts are functions in this repo — a parallel catalog that
exists precisely for "things that shell out to other binaries" — and `devstuff functions
list` grouped by category is the menu that does the guiding.

The wizard is the minimum needed to make them usable: without it every function would take
host, user, port, auth and node as parameters, which is the tedium the functions exist to
remove.

- **Rejected — one `pve` dispatcher function with a subcommand parameter.** Fewer catalog
  entries and one script, but `devstuff functions list` would show a single opaque row.
  Discoverability *is* the feature here.
- **Rejected — a first-class tool entry in `tools.yaml`.** There is nothing to install: the
  client side is `ssh` and `curl`. A catalog entry that installs nothing would break the
  one promise every catalog entry makes.

## SD-2 — The shared shell helper is sourced from the installed package, never copied

**Decision: `configure/proxmox/lib.sh` and `fmt.py` ship as package data. Each function
starts by asking devstuff where they are, and sources them in place.**

Fourteen functions need one transport layer, one confirmation helper and one guest resolver
— roughly 300 lines that cannot be duplicated fourteen times in YAML. The obvious answer is
for the wizard to write a helper into `~/.config/devstuff/`, which every function then
sources.

That answer has a slow failure mode: upgrade devstuff, and the functions from the new
version run against the helper the old version wrote. Every mitigation (a version marker, a
staleness check, re-installing it on every run) is machinery whose only job is to detect a
problem that does not need to exist.

Sourcing from the installed package makes the helper and the functions the same artifact,
versioned together, with nothing to drift. It also means the helper is a real `.sh` file in
the repo — shell-highlighted, `bash -n`-checkable, and directly testable — rather than a
Python string that renders one.

- **Rejected — generate the helper into the config dir.** Drift, per above.
- **Rejected — inline the shared code into all fourteen `script:` bodies.** Fourteen copies
  of the confirmation guard is fourteen places for it to be subtly wrong.

**Consequence:** the functions need to locate the package, which is SD-3.

## SD-3 — The shell/Python boundary is one generic `--export` flag

**Decision: `configure_cmd` gains `--export [PROFILE]`, which asks the configurator module
for shell assignments and prints them to stdout. Each function begins:**

```bash
_env="$("${DEVSTUFF_BIN:-devstuff}" configure proxmox --export "${profile:-}")" || exit 1
eval "$_env"
. "$PVE_LIB" || exit 1
```

This single call answers four questions at once: where the package is (`PVE_LIB`,
`PVE_FMT`), which interpreter to parse JSON with (`PVE_PYTHON`, from `sys.executable` —
guaranteed present, guaranteed ≥3.11), what the chosen profile's connection details are, and
whether the profile is valid at all. A bad profile fails here, before the function has done
anything, with the error on stderr and a non-zero exit.

Critically, **it exports where the secret lives, not the secret**. `PVE_SECRET_ENV` /
`PVE_SECRET_FILE` are references; `lib.sh` dereferences one at the moment it builds the
request. The secret never transits this bridge, so it is absent from the `eval` text, from
`set -x` traces, and from `devstuff -vv` output.

`--export` is generic rather than `--proxmox-export`: it is the mirror of the existing
`--path` flag (`config_path()`), it calls an optional `export()` on the module, and it errors
cleanly for the seven configurators that do not define one.

- **Rejected — parse `proxmox.yaml` in bash.** Needs `yq`, which is SD-5.
- **Rejected — a new top-level command (`devstuff pve …`).** The functions *are* the
  command surface; a second one competing with them is the worst of both.
- **Rejected — put the secret in the exported environment.** It would work, and it would put
  the token in the output of a command users will inevitably run by hand to debug.

**Consequence:** `function_runner` exports `DEVSTUFF_BIN` (resolved from `sys.argv[0]`) so
this works when devstuff is run from source via `uv run` and is not on `PATH`. That is a
generic improvement — any function may now call back into devstuff.

## SD-4 — The wizard's connection test runs `lib.sh`, not a Python reimplementation

**Decision: `validate.py` builds the profile's environment and runs
`bash -c '. "$PVE_LIB"; pve_selftest'`, parsing its tab-separated results.**

This is the local form of the rule the starship configurator states as "preview with the
real binary". If the wizard tested connectivity with `urllib` and the functions used `curl`,
a green check would prove something adjacent to what the user is about to do — TLS
verification, proxy handling, and the `-K -` credential path all differ between the two.
Running the functions' own transport means a passing test is the thing itself passing.

It also means `pve-check` (FR-24) and the wizard's test are, unavoidably, the same code.

- **Rejected — `urllib` in `validate.py`.** No new dependency either way, but two
  implementations of one thing, and the one under test would be the wrong one.

## SD-5 — JSON is parsed by devstuff's own interpreter; `jq`/`yq` are not dependencies

**Decision: `fmt.py` is a standalone stdlib-only script run as `"$PVE_PYTHON" "$PVE_FMT"
<view>`, reading JSON on stdin.**

Everything the functions read is JSON — `pvesh get --output-format json` on the SSH side and
`/api2/json/…` on the API side — so something has to parse it.

`yq` is a devstuff catalog tool and was the obvious candidate, until it turned out that
**`yq` names two unrelated programs with incompatible syntax**: mikefarah's Go implementation
(what `devstuff install yq` installs, `yq -p json '.a[0].b'`) and python-yq, a jq wrapper
packaged by most distros, which reports its version as `yq 0.0.0` and rejects that
invocation. The development container for this very repo has the second one. A dependency
that silently means a different program on a substantial fraction of machines is not a
dependency worth taking for pretty-printing.

`PVE_PYTHON` is `sys.executable` — the interpreter already running devstuff. It is present
by construction, it is at least 3.11, and its `json` module needs nothing installed.

- **Rejected — require mikefarah `yq`, detect the wrong one, and tell the user.** Workable,
  and it is what the catalog's own `validate-yaml` function would imply, but it makes basic
  inventory listing depend on the user resolving a naming collision they did not cause.
- **Rejected — `jq`.** Not in the catalog, so the remedy would be a distro package name
  rather than a devstuff command.
- **Rejected — table formatting in bash with `awk`.** The output includes byte and duration
  rendering, column alignment across unicode names, and sorting. That is a program.

## SD-6 — The API path table was extracted from Proxmox's own schema

**Decision: every path, parameter, returned field and required privilege in `model.py` was
read out of `pve-docs/api-viewer/apidoc.js` — the schema Proxmox generates from its source —
by script, not typed from memory.**

With no live node to interrogate, this is the closest available thing to measuring the
binary, and it immediately paid for itself:

- **`GET /nodes/{node}/apt/update` requires `Sys.Modify`, not `Sys.Audit`.** Listing
  *pending updates* — unambiguously a read — needs a write privilege. A token scoped
  `PVEAuditor`, which is exactly what a careful user creates for a read-only integration,
  gets a 403 from `pve-updates` and nothing else. So the operation table carries the
  privilege each read needs, `pve-updates` explains that specific 403 rather than reporting
  a generic failure, and the wizard's token test reports it per-operation (FR-11).
- **`/cluster/resources?type=vm` returns containers too**, tagged `type: qemu | lxc`. One
  request resolves name → VMID → node → which binary to use, which is the whole of FR-17.
  The `type` *filter* value (`vm`) and the `type` *field* values (`qemu`/`lxc`) are different
  vocabularies in the same call.
- **`lxc/{vmid}/snapshot` has no `vmstate` field** where `qemu`'s does — the API agreeing
  with `pct.1` that containers cannot snapshot RAM.
- **`/nodes/{node}/storage` returns `used_fraction`**, so nothing computes a percentage.

- **Rejected — hand-write the paths from the wiki.** Three of the four findings above are
  invisible in prose documentation.

## SD-7 — Confirmation requires stdin *and* stdout to be a TTY

**Decision: `pve_confirm` proceeds only if `[ -t 0 ] && [ -t 1 ]`, or
`DEVSTUFF_PVE_ASSUME_YES=1` is set. Otherwise it refuses and exits non-zero.**

Testing stdin alone is the obvious guard and it is not enough, because of a caller this repo
already has: `devstuff agent` exposes every `script` function to a local model as a tool, and
runs it with `capture=True` — stdout is a pipe while **stdin is still the REPL's terminal**.
A stdin-only check passes there, and the function then blocks forever on a prompt written
into a captured buffer that nobody can see. Requiring both makes that case a clean refusal.

The env var is the deliberate way through, for cron and scripts. It is a variable and not a
parameter so that it cannot be set by the agent, which controls arguments but not the
environment.

- **Rejected — `-t 0` only.** Hangs the agent, per above.
- **Rejected — no escape hatch.** Makes every function unusable from a script, which is a
  strange property for a script.
- **Rejected — a `--dry-run`-style parameter defaulting to execute.** Considered and turned
  down when the safety posture was chosen: the failure mode is a `stop` against the wrong
  VMID, and a flag you have to remember to pass does not prevent it.

## SD-8 — SSH is the write path; the API transport is read-only

**Decision: `api` profiles serve the seven read operations. The seven state-changing ones
require `ssh` and say so.**

Proxmox's API can start, stop, snapshot and migrate perfectly well. Supporting it would mean
every operation implemented twice — a `qm`/`pct` command *and* a POST with its own parameter
spelling — and then tested twice, against a node that is not available to test against.

The read/write split is where the transports genuinely differ in value. Reads over the API
need no shell access and can use a scoped token, which is the reason to want an API profile
at all. Writes over SSH get `vzdump`'s streaming progress, real exit codes, and the exact
commands the functions are trying to teach — a POST to `/nodes/pve1/qemu/101/status/shutdown`
teaches nothing transferable.

- **Rejected — full parity.** Double the surface for the transport that benefits least.
- **Rejected — API-only writes** (better task IDs, granular token scopes). It would make the
  printed command an HTTP request, abandoning SD-1's stated purpose.

**Consequence:** the refusal in `pve_require_ssh` must be specific — it names the profile,
its transport, and that an SSH profile is what the operation needs. "Not supported" would be
a bug report waiting to happen.

## SD-9 — Guest names are resolved, never assumed unique

**Decision: resolution returns every match; exactly one proceeds, two or more print the
candidates and exit 2.**

Proxmox does not enforce unique guest names, and the machines most likely to collide are the
ones most likely to be confused for each other (`debian-template` on three nodes, a `web01`
kept in two clusters). Picking the first match — by VMID, by node order, by anything — is a
coin flip that runs a state change against the loser.

Exiting with the candidates listed costs the user one retype with a VMID and makes the
ambiguity visible exactly once.

- **Rejected — prefer the running one / the lowest VMID.** A heuristic that is right most of
  the time is worse than no heuristic here, because it is only wrong when it matters.
