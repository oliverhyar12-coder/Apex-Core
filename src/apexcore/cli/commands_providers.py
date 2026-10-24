"""``apex providers`` and ``apex models``."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from apexcore.cli.common import guard
from apexcore.cli.ui import sanitize, stdout_console
from apexcore.config import (
    PROVIDER_NAME_RE,
    ProviderConfig,
    load_settings,
    read_config_file,
    validate_config_data,
    write_config_file,
)
from apexcore.errors import ConfigError
from apexcore.platform_utils import call_interruptibly
from apexcore.providers.factory import create_provider, resolve_provider
from apexcore.providers.presets import ADAPTERS, OPENAI_COMPATIBLE, PRESETS, get_preset
from apexcore.secrets import lookup_api_key

app = typer.Typer(
    help="List, add, remove, and select model providers (`apex providers` alone lists them).",
    invoke_without_command=True,
)


@app.callback()
def providers_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        list_()


@app.command("list")
@guard
def list_() -> None:
    """Show configured providers and the built-in presets."""
    settings = load_settings()
    console = stdout_console()
    table = Table(title="Providers", title_justify="left")
    for column in ("", "name", "adapter", "model", "API key", "base URL"):
        table.add_column(column)
    names = list(settings.providers) + [p for p in PRESETS if p not in settings.providers]
    for name in names:
        configured = name in settings.providers
        cfg = settings.providers.get(name) or ProviderConfig.from_preset(PRESETS[name])
        if cfg.api_key_env:
            lookup = lookup_api_key(name, cfg.api_key_env)
            key = f"{cfg.api_key_env}: {'set' if lookup.found else 'missing'}"
        else:
            key = "not needed"
        marker = "*" if name == settings.default_provider else ("+" if configured else "")
        table.add_row(
            marker,
            Text(name, style="bold" if configured else "dim"),
            cfg.adapter,
            Text(cfg.model or "-"),
            Text(key),
            Text(cfg.base_url),
        )
    console.print(table)
    console.print(
        Text(
            "* default   + configured   (dim = preset, usable with --provider NAME --model M)",
            style="dim",
        )
    )


@app.command("add")
@guard
def add(
    name: Annotated[str, typer.Argument(help="Name for this provider, e.g. openai or mylocal")],
    preset: Annotated[
        str | None, typer.Option(help=f"Start from a preset: {', '.join(PRESETS)}")
    ] = None,
    base_url: Annotated[str | None, typer.Option(help="API base URL")] = None,
    model: Annotated[str | None, typer.Option(help="Default model")] = None,
    api_key_env: Annotated[
        str | None,
        typer.Option(help="NAME of the environment variable holding the key ('' for none)"),
    ] = None,
    adapter: Annotated[str | None, typer.Option(help=f"One of: {', '.join(ADAPTERS)}")] = None,
    make_default: Annotated[bool, typer.Option("--default", help="Make it the default")] = False,
    force: Annotated[bool, typer.Option(help="Replace an existing entry")] = False,
) -> None:
    """Add a provider to config.toml. Presets fill in sensible defaults."""
    name = name.lower()
    if not PROVIDER_NAME_RE.match(name):
        raise ConfigError("Provider names use lowercase letters, digits, '-' and '_'.")
    data = read_config_file()
    providers = data.setdefault("providers", {})
    if name in providers and not force:
        raise ConfigError(f"Provider {name!r} already exists.", hint="Use --force to replace it.")
    base = get_preset(preset or name)
    if preset and base is None:
        raise ConfigError(f"Unknown preset {preset!r}.", hint=f"Presets: {', '.join(PRESETS)}")
    if base is None and not base_url:
        raise ConfigError(
            f"{name!r} is not a preset, so --base-url is required.",
            hint="For example: --base-url http://localhost:8000/v1",
        )
    entry: dict[str, object] = {}
    if base is not None:
        entry["base_url"] = base.base_url
        if base.adapter != OPENAI_COMPATIBLE:
            entry["adapter"] = base.adapter
        if base.api_key_env:
            entry["api_key_env"] = base.api_key_env
        if base.suggested_model:
            entry["model"] = base.suggested_model
    for key, value in (("base_url", base_url), ("model", model), ("adapter", adapter)):
        if value:
            entry[key] = value
    if api_key_env is not None:
        if api_key_env:
            entry["api_key_env"] = api_key_env
        else:
            entry.pop("api_key_env", None)
    providers[name] = entry
    if make_default or not data.get("default_provider"):
        data["default_provider"] = name
    write_config_file(data)
    console = stdout_console()
    console.print(Text(f"Added provider {name!r}.", style="green"))
    if data.get("default_provider") == name:
        console.print("It is now the default provider.")
    if not entry.get("model"):
        console.print(
            Text(
                f"No model set yet: run `apex models --provider {name}`, then "
                f"`apex config set providers.{name}.model <model>`.",
                style="yellow",
            )
        )


@app.command("remove")
@guard
def remove(name: Annotated[str, typer.Argument(help="Provider to remove")]) -> None:
    """Remove a provider from config.toml (stored keys are left alone)."""
    data = read_config_file()
    providers = data.get("providers", {})
    if name not in providers:
        raise ConfigError(f"Provider {name!r} is not configured.")
    del providers[name]
    if data.get("default_provider") == name:
        data.pop("default_provider")
    write_config_file(data)
    stdout_console().print(Text(f"Removed provider {name!r}.", style="green"))


@app.command("use")
@guard
def use(name: Annotated[str, typer.Argument(help="Provider to make the default")]) -> None:
    """Set the default provider."""
    data = read_config_file()
    if name not in data.get("providers", {}):
        raise ConfigError(
            f"Provider {name!r} is not configured.",
            hint=f"Add it first: apex providers add {name}",
        )
    data["default_provider"] = name
    validate_config_data(data)
    write_config_file(data)
    stdout_console().print(Text(f"Default provider is now {name!r}.", style="green"))


@guard
def models(
    provider: Annotated[str | None, typer.Option("--provider", "-p", help="Provider name")] = None,
) -> None:
    """List the models a provider offers (one free API request)."""
    settings = load_settings()
    resolved = resolve_provider(settings, provider)
    console = stdout_console()
    with create_provider(resolved.name, resolved.config) as client:
        items = call_interruptibly(client.list_models, client.cancel)
    if not items:
        console.print(f"{resolved.name} returned no models.")
        return
    table = Table(title=f"Models from {resolved.name}", title_justify="left")
    table.add_column("")
    table.add_column("id")
    table.add_column("details")
    for item in items:
        marker = "*" if item.id == resolved.model else ""
        table.add_row(marker, Text(sanitize(item.id)), Text(sanitize(item.description or "")))
    console.print(table)
    if resolved.model:
        console.print(Text(f"* = configured model ({resolved.model})", style="dim"))
    console.print(
        Text(f"Set one with: apex config set providers.{resolved.name}.model <id>", style="dim")
    )
