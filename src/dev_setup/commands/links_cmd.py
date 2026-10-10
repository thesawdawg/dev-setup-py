from __future__ import annotations

import sys
import webbrowser

import click
import questionary
from rich.markup import escape

from dev_setup import links_registry, ui
from dev_setup.links_catalog import USER_CATALOG_PATH
from dev_setup.links_registry import LinkDef


@click.group("links", invoke_without_command=True)
@click.pass_context
def links_cmd(ctx: click.Context) -> None:
    """Browse, search, and open links to other tools' sites and docs."""
    if ctx.invoked_subcommand is not None:
        return
    # A bare `devstuff links` is the "access the section" entry point: a type-to-filter
    # picker when there is a terminal to drive it, the plain listing otherwise.
    if sys.stdin.isatty() and sys.stdout.isatty():
        _pick_and_open()
    else:
        ctx.invoke(list_cmd)


def _print_groups(links: list[LinkDef]) -> None:
    by_cat: dict[str, list[LinkDef]] = {}
    for link in links:
        by_cat.setdefault(link.category, []).append(link)

    key_width = max(len(link.key) for link in links) + 2
    for cat in sorted(by_cat, key=lambda c: (c == "custom", c)):
        ui.console.print(f"\n  [bold]{cat.upper()}[/]")
        for link in sorted(by_cat[cat], key=lambda link: link.key):
            _print_row(link, key_width)
    ui.console.print()


def _print_row(link: LinkDef, key_width: int, *, show_category: bool = False) -> None:
    ui.console.print(f"  [bold cyan]{link.key:<{key_width}}[/] {link.name}")
    detail = link.description
    if show_category:
        detail = f"{detail}  [{link.category}]" if detail else f"[{link.category}]"
    if detail:
        # escape(): "[category]" and any bracketed description text are valid Rich markup
        ui.console.print(f"  {'':<{key_width}} [dim]{escape(detail)}[/]")


@links_cmd.command("list")
@click.argument("category", required=False)
def list_cmd(category: str | None) -> None:
    """List links, grouped by category."""
    links = links_registry.all_links()
    if category:
        links = [link for link in links if link.category == category]
    if not links:
        ui.info("No links match." if category else "No links defined.")
        return
    _print_groups(links)
    ui.dim("Open one with:  devstuff links open <key>")


@links_cmd.command("search")
@click.argument("query", nargs=-1, required=True)
def search_cmd(query: tuple[str, ...]) -> None:
    """Search links by title and description. Every word must match."""
    text = " ".join(query)
    matches = links_registry.search(text)
    # "Found nothing" is a correct answer, so it exits 0 — non-zero is for failures.
    if not matches:
        ui.info(f"No links match '{text}'.")
        return
    ui.console.print(f"\n  [bold]{len(matches)} match(es)[/] [dim]for '{escape(text)}'[/]")
    key_width = max(len(link.key) for link in matches) + 2
    for link in matches:  # keep search rank order rather than re-sorting by key
        _print_row(link, key_width, show_category=True)
    ui.console.print()
    ui.dim("Open one with:  devstuff links open <key>")


@links_cmd.command("open")
@click.argument("key")
def open_cmd(key: str) -> None:
    """Open a link in your browser."""
    link = links_registry.get(key)
    if link is None:
        ui.error(f"Unknown link: '{key}'")
        suggestions = [m.key for m in links_registry.search(key)][:5]
        if suggestions:
            ui.dim(f"Did you mean: {', '.join(suggestions)}")
        ui.dim("See all links with:  devstuff links list")
        sys.exit(1)
    _open(link)


@links_cmd.command("path")
def path_cmd() -> None:
    """Print the path to the user links catalog."""
    click.echo(str(USER_CATALOG_PATH))


def _open(link: LinkDef) -> None:
    ui.console.print(f"\n  [bold]{link.name}[/]")
    if link.description:
        ui.console.print(f"  [dim]{escape(link.description)}[/]")
    ui.console.print(f"  [cyan underline link={link.url}]{link.url}[/]\n")

    # webbrowser.open acts on the machine devstuff runs on: over SSH there is no browser
    # to find, so the URL above is the fallback (terminals make it clickable).
    if webbrowser.open(link.url):
        ui.success("Opened in browser.")
    else:
        ui.warn("Could not open a browser — copy the URL above to open it manually.")
        sys.exit(1)


def _pick_and_open() -> None:
    links = sorted(
        links_registry.all_links(),
        key=lambda link: (link.category == "custom", link.category, link.key),
    )
    if not links:
        ui.info("No links defined.")
        return

    ui.section("Links")
    ui.dim("Type to filter, Enter to open.")
    choices = [
        questionary.Choice(
            title=f"{link.name}  ·  {link.category}  ·  {link.description}",
            value=link.key,
        )
        for link in links
    ]
    key = ui.select("Open which link?", choices, searchable=True)
    link = links_registry.get(key)
    if link is not None:
        _open(link)
