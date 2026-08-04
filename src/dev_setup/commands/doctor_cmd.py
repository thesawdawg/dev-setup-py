from __future__ import annotations

import sys

import click

from dev_setup import doctor, ui

_STATUS_ICON = {
    doctor.PASS: "[green bold]✔[/]",
    doctor.WARN: "[yellow bold]⚠[/]",
    doctor.FAIL: "[red bold]✖[/]",
}
_STATUS_LABEL = {doctor.PASS: "pass", doctor.WARN: "warn", doctor.FAIL: "FAIL"}


@click.command("doctor")
@click.option("--fix", "auto_fix", is_flag=True, help="Apply fixes without prompting.")
@click.option("--check-only", is_flag=True, help="Run checks only; don't offer or apply fixes.")
def doctor_cmd(auto_fix: bool, check_only: bool) -> None:
    """Diagnose the devstuff installation and optionally fix problems.

    Runs a battery of health checks (Python version, dependencies, catalog
    validation, config directory, bashrc, and leftover dev-setup artifacts from
    pre-v1.19 installs). Each check is reported as pass / warn / fail. Checks
    that carry an auto-fix are offered for repair unless --check-only is set;
    --fix applies them all without prompting.
    """
    ui.section("devstuff doctor")
    results = doctor.run_all_checks()

    fixable: list[doctor.CheckResult] = []
    for r in results:
        icon = _STATUS_ICON.get(r.status, "[dim]?[/]")
        label = _STATUS_LABEL.get(r.status, r.status)
        ui.console.print(f"  {icon}  [bold]{r.name}[/]  [dim]({label})[/]  {r.message}")
        if r.detail:
            ui.dim(f"     {r.detail}")
        if r.status != doctor.PASS and r.fix is not None:
            fixable.append(r)

    passed = sum(1 for r in results if r.status == doctor.PASS)
    warned = sum(1 for r in results if r.status == doctor.WARN)
    failed = sum(1 for r in results if r.status == doctor.FAIL)
    ui.console.print()
    ui.console.print(
        f"  [dim]{passed} passed, {warned} warning(s), {failed} failure(s)[/]"
    )

    if check_only or not fixable:
        if failed:
            sys.exit(1)
        return

    ui.divider()
    if not auto_fix:
        ui.info(f"{len(fixable)} check(s) can be auto-fixed:")
        for r in fixable:
            ui.dim(f"  • {r.name}: {r.message}")

    applied = 0
    failed_fixes = 0
    for r in fixable:
        if not auto_fix:
            ui.console.print()
            if not ui.confirm(f"Fix: {r.name}?", default=False):
                ui.dim("Skipped.")
                continue
        assert r.fix is not None  # guarded by fixable membership
        try:
            ok = r.fix()
        except Exception as exc:
            ui.error(f"Fix for {r.name} raised: {exc}")
            ok = False
        if ok:
            ui.success(f"Fixed: {r.name}")
            applied += 1
        else:
            ui.error(f"Fix failed: {r.name}")
            failed_fixes += 1

    ui.divider()
    if failed_fixes:
        ui.error(f"{applied} fix(es) applied, {failed_fixes} failed.")
        sys.exit(1)
    if applied:
        ui.success(f"All {applied} fix(es) applied successfully.")
    else:
        ui.success("No fixes needed.")
    # Unfixable failures (e.g. a corrupt catalog) still mean the installation
    # is unhealthy, even if every available fix succeeded.
    if failed:
        sys.exit(1)
