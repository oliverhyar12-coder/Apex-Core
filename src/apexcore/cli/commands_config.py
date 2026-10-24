"""``apex config``: view and edit settings and credentials."""

from __future__ import annotations

import sys
from typing import Annotated, Any

import tomli_w
import typer
from rich.text import Text

from apexcore import paths
from apexcore.cli.common import guard
from apexcore.cli.ui import stdout_console
from apexcore.config import (
    get_dotted,
    load_settings,
    set_config_value,
    unset_config_value,
)
from apexcore.errors import ConfigError
from apexcore.providers.factory import provider_config
from apexcore.secrets import (
    KEYRING_INSTALL_HINT,
    delete_from_env_file,
    delete_from_keyring,
    describe_source,
    keyring_available,
    lookup_api_key,
    store_in_env_file,
    store_in_keyring,
)

app = typer.Typer(
    help="View and edit configuration (`apex config` alone shows it).",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _effective_dict() -> dict[str, Any]:
    return load_settings().model_dump(mode="json", exclude_none=True)


@app.callback()
def config_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        show()


@app.command("show")
@guard
def show() -> None:
    """Show the effective configuration (file + APEX_* variables) and key status."""
    console = stdout_console()
    settings = load_settings()
    console.print(Text(f"# {paths.config_file()}", style="dim"))
    if not paths.config_file().exists():
        console.print(Text("# (file does not exist yet; run `apex init`)", style="dim"))
    console.print(Text(tomli_w.dumps(_effective_dict())))
    if settings.providers:
        console.print(Text("# API keys (values are never shown)", style="dim"))
        for name, cfg in settings.providers.items():
            if not cfg.api_key_env:
                console.print(Text(f"#   {name}: no key needed"))
                continue
            lookup = lookup_api_key(name, cfg.api_key_env)
            state = describe_source(lookup, cfg.api_key_env)
            console.print(Text(f"#   {name}: {cfg.api_key_env} -> {state}"))


@app.command("path")
@guard
def path() -> None:
    """Print where configuration, credentials, and history are stored."""
    console = stdout_console()
    rows = [
        ("config file", paths.config_file()),
        ("credentials file", paths.env_file()),
        ("data directory", paths.data_dir()),
        ("history database", paths.history_db()),
    ]
    for label, value in rows:
        console.print(Text(f"{label + ':':<18} ") + Text(str(value)))
    console.print(
        Text(f"Override with {paths.CONFIG_DIR_ENV} and {paths.DATA_DIR_ENV}.", style="dim")
    )


@app.command("get")
@guard
def get(key: Annotated[str, typer.Argument(help="Dotted key, e.g. agent.max_steps")]) -> None:
    """Print one effective setting."""
    try:
        value = get_dotted(_effective_dict(), key)
    except KeyError:
        raise ConfigError(f"{key!r} is not set.", hint="See `apex config show`.") from None
    if isinstance(value, dict | list):
        stdout_console().print(Text(tomli_w.dumps({key.split(".")[-1]: value}).strip()))
    else:
        typer.echo(value)


@app.command("set")
@guard
def set_(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. providers.openai.model")],
    value: Annotated[str, typer.Argument(help="Value; TOML syntax for numbers, lists, booleans")],
) -> None:
    """Set a value in config.toml (validated before saving)."""
    stored = set_config_value(key, value)
    stdout_console().print(Text(f"Set {key} = {stored!r}", style="green"))


@app.command("unset")
@guard
def unset(key: Annotated[str, typer.Argument(help="Dotted key to remove")]) -> None:
    """Remove a value from config.toml (reverting to the default)."""
    if unset_config_value(key):
        stdout_console().print(Text(f"Removed {key}.", style="green"))
    else:
        raise ConfigError(f"{key!r} is not set in {paths.config_file()}.")


@app.command("set-key")
@guard
def set_key(
    provider: Annotated[str, typer.Argument(help="Provider name, e.g. openai")],
    env_file: Annotated[
        bool,
        typer.Option(
            "--env-file",
            help="Store in the private credentials file instead of the OS keyring.",
        ),
    ] = False,
    from_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the key from standard input.")
    ] = False,
) -> None:
    """Store an API key in the OS keyring (or the credentials file). Never in config.toml."""
    settings = load_settings()
    cfg = provider_config(settings, provider)
    if not cfg.api_key_env:
        raise ConfigError(f"Provider {provider!r} does not use an API key.")
    if not env_file and not keyring_available():
        raise ConfigError(
            "No usable OS keyring is available.",
            hint=f"To add it, {KEYRING_INSTALL_HINT}. Or use --env-file to store the key "
            f"in {paths.env_file()}, or set {cfg.api_key_env}.",
        )
    if from_stdin:
        value = sys.stdin.readline().strip()
    else:
        value = typer.prompt(f"API key for {provider}", hide_input=True).strip()
    if not value:
        raise ConfigError("No key entered.")
    if env_file:
        store_in_env_file(cfg.api_key_env, value)
        where = str(paths.env_file())
    else:
        store_in_keyring(provider, value)
        where = "the OS keyring"
    stdout_console().print(Text(f"Stored the {provider} key in {where}.", style="green"))
    env_value = lookup_api_key(provider, cfg.api_key_env)
    if env_value.source == "env":
        stdout_console().print(
            Text(
                f"Note: {cfg.api_key_env} is also set in your environment and takes priority.",
                style="yellow",
            )
        )


@app.command("delete-key")
@guard
def delete_key(provider: Annotated[str, typer.Argument(help="Provider name")]) -> None:
    """Remove a stored API key from the OS keyring and the credentials file."""
    settings = load_settings()
    cfg = provider_config(settings, provider)
    removed = []
    if delete_from_keyring(provider):
        removed.append("OS keyring")
    if cfg.api_key_env and delete_from_env_file(cfg.api_key_env):
        removed.append("credentials file")
    console = stdout_console()
    if removed:
        console.print(
            Text(f"Removed the {provider} key from: {', '.join(removed)}.", style="green")
        )
    else:
        console.print(f"No stored key found for {provider}.")
    if cfg.api_key_env and lookup_api_key(provider, cfg.api_key_env).source == "env":
        console.print(
            Text(
                f"{cfg.api_key_env} is still set in your environment (unset it in your shell).",
                style="yellow",
            )
        )
