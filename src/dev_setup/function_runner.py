from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable

from dev_setup.base import patch_bashrc, remove_bashrc_block
from dev_setup.functions_registry import FunctionDef, FunctionParam


class ParamResolutionError(RuntimeError):
    pass


def resolve_params(
    params: list[FunctionParam],
    args: tuple[str, ...],
    *,
    prompt: Callable[[FunctionParam], str] | None = None,
) -> list[str]:
    """Resolve one value per declared param, filling any gap via `prompt` or a catalog
    default. When `prompt` is None (the eval codepath, which must keep stdout clean),
    missing required params raise instead of being asked for interactively.

    A value counts as "provided" only if it's non-empty — an explicitly empty positional
    arg (`dev-setup run key ""`) is treated the same as a missing one for a required
    param, rather than silently running with an empty value.
    """
    positional = list(args[: len(params)])
    positional += [""] * (len(params) - len(positional))

    values: list[str] = []
    missing: list[str] = []
    for p, value in zip(params, positional, strict=True):
        if not value and prompt is not None:
            value = prompt(p)
        if not value and p.default:
            value = p.default
        if not value and p.required:
            missing.append(p.name)
        values.append(value)

    if missing:
        raise ParamResolutionError("Missing required parameter(s): " + ", ".join(missing))
    return values


def _positional_prelude(params: list[FunctionParam]) -> str:
    """`name="$1"`-style assignments mapping real argv positions to the script's named vars.

    Used for `script`-type subprocess runs and bashrc-registered functions, both of which
    receive their values as real shell positional arguments at call time.
    """
    return "\n".join(f'{p.name}="${i + 1}"' for i, p in enumerate(params))


def _literal_prelude(params: list[FunctionParam], values: list[str]) -> str:
    """`name='resolved value'` assignments for eval mode.

    `eval "$(dev-setup run ...)"` has no argv of its own — the values dev-setup resolved
    from its own CLI args must be baked into the printed text as shell-quoted literals.
    """
    return "\n".join(f"{p.name}={shlex.quote(v)}" for p, v in zip(params, values, strict=True))


def render_eval_script(fn: FunctionDef, args: tuple[str, ...]) -> str:
    """Build the text printed for `register: eval` mode. Never prompts — stdout must stay
    clean for `eval "$(...)"` capture, so missing required params raise instead."""
    values = resolve_params(fn.params, args)
    prelude = _literal_prelude(fn.params, values)
    return f"{prelude}\n{fn.script}" if prelude else fn.script


def _bashrc_prelude(fn: FunctionDef) -> list[str]:
    """Per-param lines for a bashrc-registered function: `name="$N"` plus, for required
    params with no default, a guard that fails loudly if the caller left it blank.

    dev-setup itself is never in the loop when an enabled function is called directly by
    the user's shell — `resolve_params` can't help here — so this is the only place
    "required" can be enforced for this mode.
    """
    lines: list[str] = []
    for i, p in enumerate(fn.params):
        lines.append(f'local {p.name}="${i + 1}"')
        if p.required and not p.default:
            lines.append(f'if [ -z "${p.name}" ]; then')
            lines.append(f'  echo "{fn.key}: missing required argument: {p.name}" >&2')
            lines.append("  return 1")
            lines.append("fi")
    return lines


def render_bashrc_function(fn: FunctionDef) -> str:
    """Build the `name() { ... }` block registered into ~/.bashrc.

    Blank lines are dropped from the body (blank-line-free is semantically identical
    in bash) because `remove_bashrc_block` treats the first blank line after the
    marker as the end of the block — a blank line inside the function would make
    `disable` orphan everything after it, closing brace included.
    """
    body_lines = [f"  {line}" for line in _bashrc_prelude(fn)]
    body_lines += [f"  {line}" for line in fn.script.rstrip("\n").splitlines() if line.strip()]
    return f"{fn.key}() {{\n" + "\n".join(body_lines) + "\n}"


def enable_bashrc_function(fn: FunctionDef) -> bool:
    return patch_bashrc(f"dev-setup-fn:{fn.key}", render_bashrc_function(fn))


def disable_bashrc_function(fn: FunctionDef) -> bool:
    return remove_bashrc_block(f"dev-setup-fn:{fn.key}")


def run_script_function(
    fn: FunctionDef,
    args: tuple[str, ...],
    *,
    prompt: Callable[[FunctionParam], str] | None = None,
    capture: bool = False,
) -> str | None:
    """Execute a `script`-type function as a subprocess. Raises on nonzero exit.

    `capture=False` (the `devstuff run` path) inherits stdio so output streams live
    and interactive prompts inside the script still reach the terminal.

    `capture=True` (the agent path) returns the combined output instead, and attaches
    it to the CalledProcessError on failure. The agent has no terminal to stream to,
    and without this a function's own diagnostics -- "yq is required, install it
    first" -- are lost, leaving the caller to guess at a bare exit code.
    """
    values = resolve_params(fn.params, args, prompt=prompt)
    prelude = _positional_prelude(fn.params)
    content = f"{prelude}\n{fn.script}" if prelude else fn.script

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        if capture:
            proc = subprocess.run(
                ["bash", tmp, *values], check=True, capture_output=True, text=True
            )
            return ((proc.stdout or "") + (proc.stderr or "")).strip()
        subprocess.run(["bash", tmp, *values], check=True)
        return None
    finally:
        os.unlink(tmp)
