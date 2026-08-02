# Stack Decisions: `devstuff configure ansible`

**Date:** 2026-08-02
**Context:** The sixth configurator. SD-1 and SD-2 in `docs/specs/starship-config/` apply
unchanged. This one is unusual in that the tool's own validator turned out to be wrong in
both directions, which shapes most of what follows.

---

## SD-1 — The load-bearing check is "did ansible read this", not "is this valid"

**Decision: `verify()` runs `ansible-config validate` *and* compares what was written against
`ansible-config dump --only-changed`. The dump comparison is the one that matters.**

Validation answers "is this file well-formed". The question a config wizard has to answer is
"does this file do what it says", and for ansible those diverge badly:

    [ssh_connection]      validate: unknown section
    pipelining = True     dump:     nothing at all

    [defaults]            validate: fine
    pipelining = True     dump:     ANSIBLE_PIPELINING = True

`dump --only-changed` reports every setting ansible took from the file, with its source. If
something written does not appear there, ansible is not reading it — whatever validation
said. That comparison is a stronger guarantee than any other configurator in this repo has,
and it exists because ansible happens to expose it.

- **Rejected — validation alone.** It is the obvious reading of "use the tool's own checker",
  and it would still let a setting land somewhere inert on a future ansible that moves one.
- **Rejected — the dump alone.** Validation catches unknown *keys* within a known section,
  which the dump cannot distinguish from a key equal to its default.
- **Rejected — running a real playbook.** Proves the most and needs a host, an inventory and
  a network. `dump` is the same information without any of that.

**Consequence:** `Setting.env_name` — the name a setting appears under in the dump — is a
first-class field, `AnsibleConfig.expected_env()` is the contract, and a test moves
`pipelining` back into `[ssh_connection]` to prove the check can actually fail.

## SD-2 — A false positive from the tool is discounted, loudly, and separately disproved

**Decision: `ansible-config validate` complaints about plugin options are dropped from the
verdict, the drop is stated in the check's own detail, and the setting is separately proved
to work through `dump -t <type>`.**

`callback_result_format = yaml` is the correct modern way to get readable output — the old
answer, `stdout_callback = yaml`, names a plugin that was removed. Measured: it visibly
changes a real run's output to YAML, and `ansible-config validate` rejects it as an unknown
key, because the validator knows only *core* settings and this is a callback-plugin option.

So the validator is unreliable in both directions: it misses dead settings (SD-1) and rejects
live ones. Deferring to it unconditionally would mean either shipping a wizard that reports
its own correct output as broken, or dropping the setting that makes ansible's output
readable.

- **Rejected — drop the setting.** Loses the single most useful output improvement available,
  to satisfy a check that is wrong.
- **Rejected — ignore the complaint silently.** The user *will* run `ansible-config validate`
  and see the error. Being surprised by it is worse than being warned about it.
- **Rejected — treat the complaint as fatal.** Would make the honest answer unreachable.
- **Rejected — put it in the presets anyway.** A config that makes `ansible-config validate`
  exit nonzero has a real cost in CI. It is offered as an explicit choice with the trade-off
  stated, not applied by default.

**Consequence:** `Setting.dump_type` distinguishes core settings from plugin options; the
discounted complaint is named in the check's detail rather than vanishing; and
`AnsibleConfig.warnings()` tells the user exactly what `validate` will say and that it is
wrong.

## SD-3 — Values are written raw, never quoted

**Decision: no value in the emitted `ansible.cfg` is quoted, and the reader coerces booleans
the way ansible does rather than the way `configparser` does.**

This is the exact opposite of the bat emitter's rule (its SD notes that unquoted values with
spaces are split into arguments), which is why the two emitters are not shared. Both halves
were measured:

    inventory = "./inv"   ->  DEFAULT_HOST_LIST = ['/tmp/acfg/"./inv"']
    pipelining = "True"   ->  ANSIBLE_PIPELINING = False

The first is merely broken. The second is worse: quoting a boolean **inverts** it, because
the quotes make it a non-empty string that ansible's coercion reads as false, with no error
anywhere.

- **Rejected — quote values containing spaces.** The habit from every other config format,
  and here it silently produces a path that does not exist.
- **Rejected — use `configparser.BOOLEAN_STATES` when reading.** Close to ansible's truthy
  set but not identical, and any difference shows up as the wizard reading a value back
  differently from the tool that will act on it.

**Consequence:** `render._value()` has no quoting branch at all, and `detect._coerce()`
mirrors ansible's own truth values — including reading an unrecognised string as `False`,
which is how it agrees with ansible about `pipelining = "True"`.

## SD-4 — Retired sections are preserved and reported, never rewritten

**Decision: a section this ansible no longer reads is carried through to the output
unchanged, and reported as a failed check explaining what replaced it.**

The tempting alternative is to migrate: see `[ssh_connection] pipelining = True`, write
`[defaults] pipelining = True`. It is a one-line transformation and it would fix the most
common broken config in existence.

It is still wrong. The wizard cannot know whether that stanza is dead weight or is being read
by something else — an older ansible on a colleague's machine, a vendored virtualenv, a CI
image pinned to 2.9. Silently rewriting configuration on the user's behalf, based on the
version that happens to be installed here, is a decision that belongs to the user.

- **Rejected — migrate automatically.** Correct on this machine, silently destructive on any
  other.
- **Rejected — delete the section.** Same problem, and it also loses the evidence of what was
  intended.
- **Rejected — say nothing.** The whole point is that this failure is invisible; adding to
  the silence is the one unacceptable option.

**Consequence:** `RETIRED_SECTIONS` carries the explanation rather than a replacement rule,
`_section_checks` surfaces it, and the user makes the change.

## SD-5 — The wizard reports the *active* config, and writes the *local* one

**Decision: `config_path()` returns `./ansible.cfg`; the file ansible currently reads is
reported separately, and can differ.**

ansible takes the first of `$ANSIBLE_CONFIG`, `./ansible.cfg`, `~/.ansible.cfg`,
`/etc/ansible/ansible.cfg` — and ignores `./ansible.cfg` outright when the directory is
world-writable. So "where does ansible read from" and "where should this wizard write" are
genuinely different questions, and conflating them would mean either writing to `/etc` by
surprise or writing somewhere that will never be read.

- **Rejected — write to whatever is currently active.** A user with `~/.ansible.cfg` would
  find a project-shaped config written into their home directory.
- **Rejected — reimplement the search order to pick a target.** The world-writable rule makes
  this a behaviour, not a list, and `ansible-config dump` already answers it exactly.

**Consequence:** the search order is reproduced *as a comment in the generated file* (where
it is documentation) but never as code; `CONFIG_FILE()` from the dump is what the wizard
reports; and the world-writable warning appears both before the questions and after the save,
because it is the one condition under which a perfect file does nothing.
