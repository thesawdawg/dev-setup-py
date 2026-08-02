# Stack Decisions: `devstuff configure bat`

**Date:** 2026-08-02
**Context:** The fifth configurator, and the one closest in shape to starship's — the tool
renders, so the preview is the deliverable. SD-1 and SD-2 in `docs/specs/starship-config/`
apply unchanged and are not restated.

---

## SD-1 — The theme list is read from the binary; only its metadata is tabled

**Decision: `bat --list-themes` is authoritative at run time. `model.THEMES` is a fallback for
when bat cannot be asked, plus the light/dark classification bat does not expose.**

The obvious design is a table of 28 themes. It is wrong for one reason: `bat cache --build`
lets a user install their own, and a hardcoded list would make the wizard unable to offer a
theme the user specifically went to the trouble of adding.

The inverse — no table at all — is also wrong, because bat does not say whether a theme is
meant for a light or a dark terminal, and the light/dark *pair* is the thing worth configuring
(SD-2).

- **Rejected — ship the list only.** Cannot offer user-built themes, and goes stale on every
  bat release.
- **Rejected — read the list only, and drop light/dark.** Then the auto-theme pair has to be
  chosen from one undifferentiated list of 28, which is the blind choice the wizard exists to
  fix.
- **Rejected — classify light/dark automatically at run time.** Measured and rejected on
  evidence: mean foreground luminance classifies 24 of 28 correctly and is wrong on Solarized,
  whose two variants share a palette by design. A heuristic that is confidently wrong about a
  famous theme pair is worse than a table.

**Consequence:** `detect.known_themes()` prefers the live list; a theme in it but not in the
table is offered labelled "(yours)"; and a test asserts every *shipped* name still exists in
the installed bat, so the fallback cannot silently rot.

## SD-2 — Themes are configured as a light/dark pair by default

**Decision: the first theme question is "follow the terminal" versus "one theme, always", and
the former asks for one theme of each kind.**

bat's default is `--theme=auto`, which picks by the terminal's background at run time. A wizard
that asked only for a single theme would quietly *disable* that — a user who moves between a
dark terminal at night and a light one in daylight would get one of them wrong permanently, and
the config would be why.

- **Rejected — ask for one theme.** Simpler, and it turns off a good default without saying so.
- **Rejected — always write the pair, never a fixed theme.** Some people have one terminal and
  want one theme; forcing a second choice on them is noise.

**Consequence:** `--theme=auto` alone is never written (it is bat's default), but the pair is —
`auto` is only worth stating when it is being steered.

## SD-3 — The preview runs the real binary, exactly as starship's does

**Decision: write the candidate to a temp file, point `BAT_CONFIG_PATH` at it, run `bat`, print
its output verbatim.**

This is the same decision as SD-3 in the starship spec and for the same reason, but bat makes
it easier: unlike starship there is no shell-escaping quirk to work around, and unlike
pre-commit there is nothing slow to do. `BAT_CONFIG_PATH` exists specifically to point bat at a
different config, so the preview needs no sandbox beyond a temp directory.

- **Rejected — an offline renderer as the primary preview.** An approximation of a syntax
  highlighter is a large amount of code whose only job is to be subtly different from the real
  one.
- **Rejected — showing the config text as the preview.** That is what the "Show the generated
  config file" menu action is for. It answers "what did I set", not "what will it look like",
  and the second is the question a theme picker raises.

**Consequence:** three flags are forced on the command line, which beats the config file:
`--paging=never` (a pager inside the wizard would hijack the terminal), `--color=always` and
`--decorations=always` (bat disables both when its output is not a tty, and the preview is
captured through a pipe). Everything else comes from the candidate. The offline path is a
*description* of the decorations, clearly labelled, used only when bat is absent.

## SD-4 — `BAT_*` environment variables are cleared for the preview and reported on the review screen

**Decision: the preview runs with every `BAT_*` variable removed, and their presence is a
warning.**

bat's precedence is command line, then environment, then config file. So a user with
`BAT_THEME=Dracula` in their shell gets Dracula no matter what this wizard writes.

Two distinct problems follow, needing two distinct answers. If the preview inherited the
variable it would render the *environment's* theme while claiming to show the candidate's —
the preview would be lying. And even with a correct preview, the saved config would then have
no effect in the user's actual shell, which they need to be told.

- **Rejected — inherit the environment, as the user's real shell would.** Makes the preview
  wrong about the thing it exists to show.
- **Rejected — clear them silently.** Fixes the preview and leaves the user with a config that
  does nothing, for a reason nothing mentions.
- **Rejected — offer to unset them.** They live in a shell rc file this wizard did not write
  and cannot reliably edit.

**Consequence:** `detect.ENV_VARS`, cleared in `preview.render_live` and reported in
`_show_preview`.

## SD-5 — The shell integration is part of the wizard, not a separate concern

**Decision: `MANPAGER`, `MANROFFOPT`, `bathelp` and the `cat` alias are offered here and
written to `~/.bashrc` through `base.patch_bashrc`.**

Every other configurator in the repo writes exactly one file. This one writes two, which is a
deliberate exception: the single most valuable thing bat does — syntax-highlighted man pages —
is not expressible in its config file at all. A wizard that configured only the config file
would omit the best feature and never mention it.

- **Rejected — a `functions.yaml` entry instead.** These are environment variables and an
  alias, which must be *in* the interactive shell; that is the `shell-eval` case, and it would
  split bat's configuration across two subsystems the user has to know to visit separately.
- **Rejected — print the lines and let the user paste them.** The repo already has an
  idempotent bashrc patcher; not using it here would be arbitrary.

**Consequence:** `SHELL_BITS` in `model.py`, and two constraints that come with
`base.patch_bashrc`. Its removal helper ends a block at the first blank line, so no shell line
may be blank — a test asserts this. And the patcher is a no-op when its marker already exists,
so re-running the wizard must *remove* the old block first or the new selection would silently
not apply.

## SD-6 — Conflicting components are warned about, and swept for rather than reasoned about

**Decision: the conflict table has exactly one entry, established by running all 21 component
pairs through the real binary and reading stderr.**

The first draft of the `review` preset was "enable every component", which is the obvious way
to build such a preset. bat's own warning rejected it: `rule` draws horizontal separators that
`grid` already draws, so `rule` is invisible whenever `grid` is on.

Rather than fix that one case, all pairs were swept. Exactly one warns. That is worth knowing
precisely, because a table with one entry looks like an oversight unless the sweep is recorded.

- **Rejected — reason about which components overlap.** How the first draft shipped the bug.
- **Rejected — refuse a conflicting pair.** Same answer as the pre-commit spec's Open Question
  4: a user may have a reason. Presets are held to the stricter rule, which is a test.

**Consequence:** `COMPONENT_CONFLICTS` in `model.py` with the sweep recorded in its comment, a
warning on the review screen, a test that no shipped preset contains a pair, and a live test
asserting bat still complains about the one pair that is tabled — so if a future bat fixes it,
the table is caught being stale rather than silently over-warning.
