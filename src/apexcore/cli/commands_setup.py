"""``apex init`` and ``apex doctor``."""

from __future__ import annotations

import locale
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table
from rich.text import Text

from apexcore import DIST_NAME, __version__, paths
from apexcore.cli.common import EXIT_USAGE, guard
from apexcore.cli.session import stdin_is_interactive
from apexcore.cli.ui import stdout_console
from apexcore.config import (
    PROVIDER_NAME_RE,
    load_settings,
    read_config_file,
    write_config_file,
)
from apexcore.errors import ApexError, ConfigError
from apexcore.history import HistoryStore
from apexcore.platform_utils import (
    IS_WINDOWS,
    call_interruptibly,
    describe_platform,
    is_elevated,
    which_no_cwd,
)
from apexcore.providers.factory import create_provider, provider_config
from apexcore.providers.presets import ADAPTERS, OPENAI_COMPATIBLE, PRESETS, get_preset
from apexcore.secrets import describe_source, keyring_available, lookup_api_key


def key_instructions(env_name: str, provider: str) -> list[str]:
    if IS_WINDOWS:
        lines = [
            f'PowerShell (this session):  $env:{env_name} = "your-key"',
            f'PowerShell (persistent):    [Environment]::SetEnvironmentVariable("{env_name}", '
            '"your-key", "User")',
            f"Command Prompt (session):   set {env_name}=your-key",
        ]
    else:
        lines = [
            f'bash/zsh:  export {env_name}="your-key"   (add to ~/.bashrc or ~/.zshrc)',
            f'fish:      set -Ux {env_name} "your-key"',
        ]
    lines.append(
        f"or store it: apex config set-key {provider}  (OS keyring; --env-file for a file)"
    )
    return lines


def _choose_interactively(defaults: dict[str, Any]) -> dict[str, Any]:
    console = stdout_console()
    table = Table(title="Providers", title_justify="left", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("name")
    table.add_column("description")
    names = list(PRESETS)
    for number, name in enumerate(names, 1):
        preset = PRESETS[name]
        table.add_row(
            str(number), name, preset.label + (f" - {preset.notes}" if preset.notes else "")
        )
    table.add_row(
        str(len(names) + 1), "custom", "Any other OpenAI-compatible or Anthropic endpoint"
    )
    console.print(table)
    choice = typer.prompt("Provider (name or number)", default=defaults.get("provider") or "openai")
    if choice.isdigit() and 1 <= int(choice) <= len(names) + 1:
        choice = [*names, "custom"][int(choice) - 1]
    choice = choice.strip().lower()
    answers: dict[str, Any] = {"provider": choice}
    preset = get_preset(choice)
    if preset is None:
        answers["name"] = typer.prompt(
            "Name for this provider", default="custom" if choice == "custom" else choice
        )
        answers["base_url"] = typer.prompt("Base URL (e.g. http://localhost:8000/v1)")
        answers["adapter"] = typer.prompt(
            f"Adapter ({' / '.join(ADAPTERS)})", default=OPENAI_COMPATIBLE
        )
        answers["api_key_env"] = typer.prompt(
            "Environment variable holding the API key (leave empty for none)",
            default="",
            show_default=False,
        )
    else:
        if preset.key_url:
            console.print(Text(f"Get a key at {preset.key_url}", style="dim"))
        if preset.notes:
            console.print(Text(preset.notes, style="dim"))
    suggestion = preset.suggested_model if preset else None
    model = typer.prompt(
        "Model id (empty = decide later; `apex models` lists them)",
        default=suggestion or "",
        show_default=bool(suggestion),
    )
    answers["model"] = model.strip() or None
    return answers


@guard
def init(
    provider: Annotated[
        str | None,
        typer.Option(
            help=f"Preset ({', '.join(PRESETS)}) or 'custom'. Omit for interactive setup."
        ),
    ] = None,
    model: Annotated[str | None, typer.Option(help="Default model id")] = None,
    name: Annotated[str | None, typer.Option(help="Name to save the provider under")] = None,
    base_url: Annotated[str | None, typer.Option(help="Override the API base URL")] = None,
    api_key_env: Annotated[
        str | None, typer.Option(help="NAME of the env variable holding the key ('' for none)")
    ] = None,
    adapter: Annotated[str | None, typer.Option(help=f"One of: {', '.join(ADAPTERS)}")] = None,
    force: Annotated[bool, typer.Option(help="Replace an existing provider entry")] = False,
) -> None:
    """Set up APEX CORE: choose a provider and model, create config and history."""
    console = stdout_console()
    existing = read_config_file()
    if provider is None:
        if not stdin_is_interactive():
            console.print("Non-interactive: pass --provider (and usually --model).")
            raise typer.Exit(EXIT_USAGE)
        answers = _choose_interactively({"provider": existing.get("default_provider")})
        provider = answers["provider"]
        name = name or answers.get("name")
        base_url = base_url or answers.get("base_url")
        adapter = adapter or answers.get("adapter")
        model = model or answers.get("model")
        if api_key_env is None and "api_key_env" in answers:
            api_key_env = answers["api_key_env"]

    provider = provider.strip().lower()
    preset = get_preset(provider)
    if preset is None and provider != "custom":
        raise ConfigError(
            f"Unknown provider preset {provider!r}.",
            hint=f"Choose one of: {', '.join(PRESETS)}, or custom with --base-url.",
        )
    name = (name or provider).lower()
    if not PROVIDER_NAME_RE.match(name):
        raise ConfigError("Provider names use lowercase letters, digits, '-' and '_'.")
    entry: dict[str, Any] = {}
    if preset is not None:
        entry["base_url"] = preset.base_url
        if preset.adapter != OPENAI_COMPATIBLE:
            entry["adapter"] = preset.adapter
        if preset.api_key_env:
            entry["api_key_env"] = preset.api_key_env
        if preset.suggested_model:
            entry["model"] = preset.suggested_model
    elif not base_url:
        raise ConfigError("A custom provider needs --base-url.")
    if base_url:
        entry["base_url"] = base_url
    if adapter:
        entry["adapter"] = adapter
    if model:
        entry["model"] = model
    if api_key_env is not None:
        if api_key_env.strip():
            entry["api_key_env"] = api_key_env.strip()
        else:
            entry.pop("api_key_env", None)

    data = dict(existing)
    providers = dict(data.get("providers", {}))
    if name in providers and providers[name] != entry and not force:
        raise ConfigError(
            f"Provider {name!r} is already configured with different settings.",
            hint="Use --force to replace it, or --name to save under another name.",
        )
    providers[name] = entry
    data["providers"] = providers
    data["default_provider"] = name
    write_config_file(data)
    version = HistoryStore().initialize()

    console.print(
        Text(f"Saved provider {name!r} as the default in {paths.config_file()}", style="green")
    )
    console.print(
        Text(f"History database ready (schema v{version}): {paths.history_db()}", style="dim")
    )
    env_name = entry.get("api_key_env")
    if env_name:
        lookup = lookup_api_key(name, env_name)
        if lookup.found:
            console.print(
                Text(f"API key found ({describe_source(lookup, env_name)}).", style="green")
            )
        else:
            console.print(
                Text(
                    f"No API key found yet. Provide {env_name} in one of these ways:",
                    style="yellow",
                )
            )
            for line in key_instructions(env_name, name):
                console.print(Text("  " + line))
    if not entry.get("model"):
        console.print(
            Text(
                f"No model chosen: run `apex models`, then "
                f"`apex config set providers.{name}.model <id>`.",
                style="yellow",
            )
        )
    console.print(Text("Next: `apex doctor` to check the setup, then `apex chat`.", style="bold"))


def _check_writable(directory: Path) -> tuple[str, str]:
    try:
        paths.ensure_private_dir(directory)
        probe = directory / ".apex-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return "FAIL", f"{directory} is not writable: {exc}"
    return "OK", str(directory)


@guard
def doctor(
    online: Annotated[
        bool,
        typer.Option("--online", help="Also contact the default provider (lists its models)"),
    ] = False,
) -> None:
    """Check the installation, configuration, keys, and terminal."""
    checks: list[tuple[str, str, str]] = []
    add = checks.append
    py = sys.version_info
    add(
        (
            "OK" if py >= (3, 11) else "FAIL",
            "python",
            f"{py.major}.{py.minor}.{py.micro} ({sys.executable})",
        )
    )
    add(("OK", "apex", f"{DIST_NAME} {__version__}"))
    add(("OK", "platform", describe_platform()))
    status, detail = _check_writable(paths.config_dir())
    add((status, "config dir", detail))
    status, detail = _check_writable(paths.data_dir())
    add((status, "data dir", detail))

    settings = None
    try:
        settings = load_settings()
        state = "found" if paths.config_file().exists() else "not created yet (run `apex init`)"
        add(
            (
                "OK" if paths.config_file().exists() else "WARN",
                "config file",
                f"{paths.config_file()} ({state})",
            )
        )
    except ApexError as exc:
        add(("FAIL", "config file", exc.message))

    if settings is not None:
        name = settings.default_provider
        if not name:
            add(("FAIL", "provider", "no default provider (run `apex init`)"))
        else:
            try:
                cfg = provider_config(settings, name)
            except ApexError as exc:
                add(("FAIL", "provider", exc.message))
                cfg = None
            if cfg is not None:
                add(("OK", "provider", f"{name} ({cfg.adapter}, {cfg.base_url})"))
                add(
                    (
                        "OK" if cfg.model else "FAIL",
                        "model",
                        cfg.model or f"not set (apex config set providers.{name}.model <id>)",
                    )
                )
                if cfg.api_key_env:
                    lookup = lookup_api_key(name, cfg.api_key_env)
                    add(
                        (
                            "OK" if lookup.found else "FAIL",
                            "api key",
                            f"{cfg.api_key_env}: {describe_source(lookup, cfg.api_key_env)}",
                        )
                    )
                else:
                    add(("OK", "api key", "not needed"))
                if online:
                    try:
                        with create_provider(name, cfg) as client:
                            count = len(call_interruptibly(client.list_models, client.cancel))
                        add(("OK", "online", f"reached {name}; {count} models available"))
                    except ApexError as exc:
                        add(("FAIL", "online", exc.message))

    try:
        version = HistoryStore().initialize()
        add(("OK", "history", f"{paths.history_db()} (schema v{version})"))
    except ApexError as exc:
        add(("FAIL", "history", exc.message))

    git = which_no_cwd("git")
    add(("OK" if git else "WARN", "git", str(git) if git else "not found (git tools disabled)"))
    add(
        (
            "OK" if keyring_available() else "INFO",
            "keyring",
            "available"
            if keyring_available()
            else f"not available (optional: pipx inject {DIST_NAME} keyring)",
        )
    )
    console = stdout_console()
    encoding = getattr(sys.stdout, "encoding", None) or locale.getpreferredencoding(False)
    add(
        (
            "OK",
            "terminal",
            f"encoding {encoding}, interactive={sys.stdin.isatty() if sys.stdin else False}, "
            f"colors={console.color_system or 'none'}",
        )
    )
    if is_elevated():
        add(("WARN", "privileges", "running as root/Administrator: command execution is disabled"))

    table = Table(title="apex doctor", title_justify="left")
    table.add_column("status")
    table.add_column("check")
    table.add_column("details")
    styles = {"OK": "green", "WARN": "yellow", "FAIL": "bold red", "INFO": "cyan"}
    for status, check, details in checks:
        table.add_row(Text(status, style=styles[status]), check, Text(details))
    console.print(table)
    failures = sum(1 for status, _, _ in checks if status == "FAIL")
    if failures:
        console.print(Text(f"{failures} problem(s) found.", style="bold red"))
        raise typer.Exit(1)
    console.print(Text("All required checks passed.", style="green"))
