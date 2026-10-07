from rich.columns import Columns
from rich.text import Text

from dev_setup import ui
from dev_setup.catalog import USER_CATALOG_PATH

_CATEGORY_DESC = {
    "core": "Always-installed tools (Docker, NVM, uv)",
    "tools": "Optional utilities (lazygit, htop, bat)",
    "ai-tools": "AI coding agents and assistants",
    "customization": "Prompt and shell customization",
    "languages": "Programming language runtimes",
    "custom": "User-added packages",
}


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
        ("configure", "[tool] [--list] [--path]",             "Set up a tool with a guided wizard"),
        ("add",     "",                                        "Add a custom package (guided wizard)"),
        ("delete",  "<key>",                                  "Remove a custom package from the registry"),
        ("doctor",  "[--fix] [--check-only]",                 "Diagnose and repair the installation"),
        ("catalog", "<path|export|import>",                   "Manage YAML tool catalogs"),
        ("docs",    "<package>",                              "Open documentation in browser"),
        ("run",     "<function> [args...]",                   "Run a function/script"),
        ("functions", "<list|enable|disable|path>",           "Manage functions/scripts"),
        ("links",   "[list|search|open|path]",                "Browse, search, and open tool links"),
        ("skills",  "add",                                    "Append GitHub skills to claude/codex/pi"),
        ("agent",   "[--setup] [--dir] [--model]",             "Chat with a local tool-using model"),
        ("version", "",                                        "Show version"),
    ]
    tbl = ui.table(bordered=False, show_header=False)
    tbl.add_column("cmd", style="bold cyan", no_wrap=True)
    tbl.add_column("args", style=ui.GRAY, no_wrap=True)
    tbl.add_column("desc", ratio=1)
    for cmd, args, desc in rows:
        # args goes in as Text: strings like "[category]" are valid Rich markup
        # tags and would be silently swallowed from a plain str cell.
        tbl.add_row(f"  {cmd}", Text(args), desc)
    ui.console.print(tbl)

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
        "devstuff configure starship",
        "devstuff add",
        "devstuff delete my-tool",
        "devstuff doctor",
        "devstuff doctor --fix",
        "devstuff catalog export",
        "devstuff functions list",
        "devstuff functions enable ssh-agent-key",
        "devstuff links search docker",
        "devstuff links open uv-docs",
        "devstuff agent",
        "devstuff agent --setup",
        'devstuff agent --print "what node tools are available?"',
        "ssh-agent-key ~/.ssh/id_ed25519",
    ]
    items = [
        Text.assemble(("  $ ", ui.GRAY), (ex, "green")) for ex in examples
    ]
    ui.console.print(Columns(items, column_first=True, padding=(0, 3)))

    from dev_setup import registry

    ui.console.print()
    ui.console.print("[bold]CATEGORIES[/]")
    _ORDER = {"core": 0, "tools": 1, "custom": 999}
    cats = sorted(
        {t.category for t in registry.all_tools()},
        key=lambda c: (_ORDER.get(c, 500), c),
    )
    for cat in cats:
        desc = _CATEGORY_DESC.get(cat, "")
        ui.console.print(f"  [cyan]{cat:<14}[/] [{ui.GRAY}]{desc}[/]")

    ui.console.print()
    ui.console.print("[bold]CONFIG[/]")
    ui.console.print(f"  User catalog: [{ui.GRAY}]{USER_CATALOG_PATH}[/]\n")
