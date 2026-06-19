import click
from dev_setup import __version__


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        from dev_setup.commands.help_cmd import print_help
        print_help()


@cli.command("version")
def version_cmd() -> None:
    """Print version and exit."""
    click.echo(f"dev-setup {__version__}")


def _register_commands() -> None:
    from dev_setup.commands.list_cmd import list_cmd
    from dev_setup.commands.install_cmd import install_cmd
    from dev_setup.commands.remove_cmd import remove_cmd
    from dev_setup.commands.add_cmd import add_cmd
    from dev_setup.commands.delete_cmd import delete_cmd
    from dev_setup.commands.docs_cmd import docs_cmd

    cli.add_command(list_cmd, "list")
    cli.add_command(install_cmd, "install")
    cli.add_command(remove_cmd, "remove")
    cli.add_command(remove_cmd, "uninstall")
    cli.add_command(add_cmd, "add")
    cli.add_command(delete_cmd, "delete")
    cli.add_command(delete_cmd, "rm")
    cli.add_command(docs_cmd, "docs")


_register_commands()
