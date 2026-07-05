from dev_setup import ui
from dev_setup.catalog import USER_CATALOG_PATH


def print_help() -> None:
    ui.print_banner()
    ui.console.print("[bold]USAGE[/]")
    ui.console.print("  dev-setup [bold cyan]<command>[/] [OPTIONS] [ARGS]\n")

    ui.console.print("[bold]COMMANDS[/]")
    rows = [
        ("list",    "[--installed] [--available] [category]", "List packages"),
        ("install", "[package ...]",                          "Install packages (interactive if no args)"),
        ("remove",  "<package ...>",                          "Uninstall installed packages"),
        ("update",  "<package ...> [--version]",              "Update to latest or a specific version"),
        ("add",     "",                                        "Add a custom package (guided wizard)"),
        ("delete",  "<key>",                                  "Remove a custom package from the registry"),
        ("catalog", "<path|export|import>",                   "Manage YAML tool catalogs"),
        ("docs",    "<package>",                              "Open documentation in browser"),
        ("version", "",                                        "Show version"),
    ]
    for cmd, args, desc in rows:
        ui.console.print(
            f"  [bold cyan]{cmd:<10}[/] [dim]{args:<40}[/]  {desc}"
        )

    ui.console.print()
    ui.console.print("[bold]EXAMPLES[/]")
    examples = [
        "dev-setup list",
        "dev-setup list core",
        "dev-setup list --installed",
        "dev-setup install docker nvm",
        "dev-setup install",
        "dev-setup remove htop",
        "dev-setup update nvm",
        "dev-setup update pi --version 1.2.3",
        "dev-setup add",
        "dev-setup delete my-tool",
        "dev-setup catalog export",
    ]
    for ex in examples:
        ui.console.print(f"  [dim]$[/] [green]{ex}[/]")

    ui.console.print()
    ui.console.print("[bold]CATEGORIES[/]")
    ui.console.print("  [cyan]core[/]    Always-installed tools (Docker, NVM, uv)")
    ui.console.print("  [cyan]tools[/]   Optional utilities (PHP, Starship, htop)")
    ui.console.print("  [cyan]languages[/] Programming language runtimes")
    ui.console.print("  [cyan]custom[/]  User-added packages\n")

    ui.console.print("[bold]CONFIG[/]")
    ui.console.print(f"  User catalog: [dim]{USER_CATALOG_PATH}[/]\n")
