from __future__ import annotations

import sys

import click

from dev_setup import function_runner as runner
from dev_setup import functions_registry, ui
from dev_setup.function_runner import ParamResolutionError
from dev_setup.functions_registry import FunctionDef, FunctionParam


@click.command("run")
@click.argument("key")
@click.argument("args", nargs=-1)
def run_cmd(key: str, args: tuple[str, ...]) -> None:
    """Run a function/script. See: dev-setup functions list"""
    fn = functions_registry.get(key)
    if fn is None:
        ui.error(f"Unknown function: '{key}'")
        sys.exit(1)

    if fn.type == "script":
        _run_script(fn, args)
    elif fn.type == "python":
        _run_python(fn, args)
    elif fn.type == "shell-eval" and fn.register == "eval":
        _run_eval(fn, args)
    else:  # shell-eval + bashrc — dev-setup can't mutate the calling shell itself
        ui.error(f"'{fn.key}' is registered via ~/.bashrc, not run directly.")
        ui.dim(f"Enable it once:  dev-setup functions enable {fn.key}")
        ui.dim(f"Then call it directly in your shell:  {fn.key} ...")
        sys.exit(1)


def _prompt_param(p: FunctionParam) -> str:
    label = p.description or p.name
    suffix = "" if p.required else " (optional)"
    return ui.text_input(f"{label}{suffix}:", default=p.default, required=p.required)


def _run_script(fn: FunctionDef, args: tuple[str, ...]) -> None:
    ui.section(fn.name)
    # Only prompt when there's an actual terminal to prompt on — otherwise a missing
    # required param would hit an unreadable stdin and fail as an opaque, unrelated
    # exception instead of the clean "Missing required parameter(s)" message.
    prompt = _prompt_param if sys.stdin.isatty() else None
    try:
        runner.run_script_function(fn, args, prompt=prompt)
        ui.success(f"{fn.name} completed")
    except ParamResolutionError as exc:
        ui.error(str(exc))
        sys.exit(1)
    except Exception as exc:
        ui.error(f"'{fn.name}' failed: {exc}")
        sys.exit(1)


def _run_python(fn: FunctionDef, args: tuple[str, ...]) -> None:
    ui.section(fn.name)
    prompt = _prompt_param if sys.stdin.isatty() else None
    try:
        runner.run_python_function(fn, args, prompt=prompt)
        ui.success(f"{fn.name} completed")
    except ParamResolutionError as exc:
        ui.error(str(exc))
        sys.exit(1)
    except Exception as exc:
        ui.error(f"'{fn.name}' failed: {exc}")
        sys.exit(1)


def _run_eval(fn: FunctionDef, args: tuple[str, ...]) -> None:
    # Diagnostics must go to stderr and stdout must carry ONLY the resolved script —
    # the caller does `eval "$(dev-setup run ...)"`, so anything else printed to
    # stdout becomes part of what gets evaluated in their shell.
    try:
        script = runner.render_eval_script(fn, args)
    except ParamResolutionError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    click.echo(script)
