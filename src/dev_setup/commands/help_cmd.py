from dev_setup import ui
from dev_setup.catalog import USER_CATALOG_PATH


def print_help() -> None:
    ui.print_banner()
    ui.console.print("[bold]USAGE[/]")
    ui.console.print("  devstuff [bold cyan]<command>[/] [OPTIONS] [ARGS]\n")

    ui.console.print("[bold]COMMANDS[/]")
    rows = [
        ("list",    "[--installed] [--available] [category]", "List packages"),
        ("install", "[package ...]",                          "Install packages (interactive if no args)"),
        ("remove",  "<package ...>",                          "Uninstall installed packages"),
        ("update",  "[package ...] [--version]",              "Update packages (interactive if no args)"),
        ("add",     "",                                        "Add a custom package (guided wizard)"),
        ("delete",  "<key>",                                  "Remove a custom package from the registry"),
        ("catalog", "<path|export|import>",                   "Manage YAML tool catalogs"),
        ("docs",    "<package>",                              "Open documentation in browser"),
        ("run",     "<function> [args...]",                   "Run a function/script"),
        ("functions", "<list|enable|disable|path>",           "Manage functions/scripts"),
        ("skills",  "add",                                    "Append skills from a GitHub repo to claude/codex/pi"),
        ("version", "",                                        "Show version"),
    ]
    for cmd, args, desc in rows:
        ui.console.print(
            f"  [bold cyan]{cmd:<10}[/] [dim]{args:<40}[/]  {desc}"
        )

    ui.console.print()
    ui.console.print("[bold]EXAMPLES[/]")
    examples = [
        "devstuff list",
        "devstuff list core",
        "devstuff list --installed",
        "devstuff install docker nvm",
        "devstuff install",
        "devstuff remove htop",
        "devstuff update nvm",
        "devstuff update pi --version 1.2.3",
        "devstuff update",
        "devstuff add",
        "devstuff delete my-tool",
        "devstuff catalog export",
        "devstuff functions list",
        "devstuff functions enable ssh-agent-key",
        "ssh-agent-key ~/.ssh/id_ed25519",
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
