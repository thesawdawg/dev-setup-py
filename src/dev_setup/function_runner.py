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
    """Resolve param values positionally from `args`, filling any gap via `prompt` or a
    catalog default. When `prompt` is None (the eval codepath, which must keep stdout
    clean), missing required params raise instead of being asked for interactively.
    """
    values: list[str] = list(args[: len(params)])
    missing: list[str] = []
    for i in range(len(values), len(params)):
        p = params[i]
        if prompt is not None:
            values.append(prompt(p))
        elif p.default:
            values.append(p.default)
        elif p.required:
            missing.append(p.name)
        else:
            values.append("")
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


def render_bashrc_function(fn: FunctionDef) -> str:
    """Build the `name() { ... }` block registered into ~/.bashrc.

    Blank lines are dropped from the body (blank-line-free is semantically identical
    in bash) because `remove_bashrc_block` treats the first blank line after the
    marker as the end of the block — a blank line inside the function would make
    `disable` orphan everything after it, closing brace included.
    """
    prelude = _positional_prelude(fn.params)
    body_lines = [f"  local {line}" for line in prelude.splitlines()]
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
) -> None:
    """Execute a `script`-type function as a subprocess. Raises on nonzero exit."""
    values = resolve_params(fn.params, args, prompt=prompt)
    prelude = _positional_prelude(fn.params)
    content = f"{prelude}\n{fn.script}" if prelude else fn.script

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        subprocess.run(["bash", tmp, *values], check=True)
    finally:
        os.unlink(tmp)
