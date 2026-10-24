"""``apex setup``, ``apex model``, ``apex init`` and ``apex doctor``."""

from __future__ import annotations

import locale
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from apexcore import DIST_NAME, __version__, paths
from apexcore.cli.common import EXIT_USAGE, guard
from apexcore.cli.pickers import chat_models, pick
from apexcore.cli.session import stdin_is_interactive
from apexcore.cli.ui import stdout_console
from apexcore.config import (
    PROVIDER_NAME_RE,
    ProviderConfig,
    load_settings,
    read_config_file,
    write_config_file,
)
from apexcore.errors import ApexError, AuthenticationError, ConfigError
from apexcore.history import HistoryStore
from apexcore.platform_utils import (
    IS_WINDOWS,
    call_interruptibly,
    describe_platform,
    is_elevated,
    which_no_cwd,
)
from apexcore.providers.base import ModelInfo
from apexcore.providers.factory import create_provider, provider_config
from apexcore.providers.presets import ADAPTERS, OPENAI_COMPATIBLE, PRESETS, get_preset
from apexcore.redaction import GLOBAL_REDACTOR
from apexcore.secrets import (
    KEYRING_INSTALL_HINT,
    describe_source,
    keyring_available,
    lookup_api_key,
    store_in_env_file,
    store_in_keyring,
)


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


Ask = Callable[[str], str]


def _ask(prompt: str) -> str:
    return typer.prompt(prompt.rstrip(": "), default="", show_default=False, prompt_suffix=": ")


def _ask_secret(prompt: str) -> str:
    return typer.prompt(
        prompt.rstrip(": "), default="", show_default=False, hide_input=True, prompt_suffix=": "
    )


def _key_status(name: str, api_key_env: str | None) -> str:
    if not api_key_env:
        return "local, no API key needed"
    if lookup_api_key(name, api_key_env).found:
        return "key found"
    return ""


def _store_key(provider: str, api_key_env: str, value: str, console: Console) -> None:
    GLOBAL_REDACTOR.add(value)
    if keyring_available():
        try:
            store_in_keyring(provider, value)
        except ConfigError:
            pass
        else:
            console.print(Text("Saved the key in your OS keyring.", style="green"))
            return
    store_in_env_file(api_key_env, value)
    console.print(
        Text(f"Saved the key to {paths.env_file()} (readable only by you).", style="green")
    )


def _list_models(name: str, entry: dict[str, Any]) -> list[ModelInfo]:
    config = ProviderConfig(**entry)
    with create_provider(name, config, redactor=GLOBAL_REDACTOR) as client:
        return call_interruptibly(client.list_models, client.cancel)


def run_setup_wizard(
    console: Console | None = None,
    *,
    ask: Ask | None = None,
    ask_secret: Ask | None = None,
    welcome: bool = True,
) -> str:
    """Interactive setup: provider, API key, and model. Returns the provider name."""
    console = console or stdout_console()
    ask = ask or _ask
    ask_secret = ask_secret or _ask_secret
    existing = read_config_file()
    providers = dict(existing.get("providers", {}))
    if welcome:
        console.print(
            Panel(
                Text(
                    "Welcome to APEX CORE! Let's connect a model.\n"
                    "Cloud providers need an API key, which stays on this computer. "
                    "Ollama and LM Studio run models locally for free.",
                ),
                title="Setup",
                title_align="left",
                border_style="cyan",
            )
        )

    # 1. Provider.
    options: list[tuple[str, str]] = []
    for name, preset in PRESETS.items():
        status = _key_status(name, preset.api_key_env)
        options.append((name, preset.label + (f"  [{status}]" if status else "")))
    for name in providers:
        if name not in PRESETS:
            options.append((name, "configured custom provider"))
    options.append(("custom", "Any other OpenAI-compatible or Anthropic endpoint"))
    found = next((n for n, d in options if d.endswith("[key found]")), None)
    default = existing.get("default_provider") or found or "openai"
    while True:
        choice = pick(console, ask, options, title="Choose a provider", default=default) or ""
        choice = choice.strip().lower()
        if choice in PRESETS or choice in providers or choice == "custom":
            break
        console.print(Text(f"Unknown provider {choice!r}.", style="yellow"))

    preset = get_preset(choice)
    name = choice
    if choice in providers:
        entry: dict[str, Any] = dict(providers[choice])
    elif preset is not None:
        entry = {"base_url": preset.base_url}
        if preset.adapter != OPENAI_COMPATIBLE:
            entry["adapter"] = preset.adapter
        if preset.api_key_env:
            entry["api_key_env"] = preset.api_key_env
    else:
        name = ask("Name for this provider (e.g. myserver): ").strip().lower() or "custom"
        while not PROVIDER_NAME_RE.match(name):
            name = ask("Use lowercase letters, digits, - or _: ").strip().lower()
        entry = {"base_url": ask("Base URL (e.g. http://localhost:8000/v1): ").strip()}
        adapter = ask(f"API format ({' / '.join(ADAPTERS)}, Enter for openai-compatible): ")
        if adapter.strip():
            entry["adapter"] = adapter.strip()
        key_env = ask("Environment variable for its API key (Enter for none): ").strip()
        if key_env:
            entry["api_key_env"] = key_env
        ProviderConfig(**entry)  # validate early (raises a clear error)

    # 2. API key.
    api_key_env = entry.get("api_key_env")
    if api_key_env:
        lookup = lookup_api_key(name, api_key_env)
        if lookup.found:
            console.print(
                Text(f"Using the key from {describe_source(lookup, api_key_env)}.", style="green")
            )
        else:
            if preset and preset.key_url:
                console.print(Text(f"Get a key at {preset.key_url}", style="dim"))
            secret = ask_secret(f"Paste your {name} API key (hidden; Enter to skip): ").strip()
            if secret:
                _store_key(name, api_key_env, secret, console)
            else:
                console.print(Text(f"Skipped. Set {api_key_env} before chatting:", style="yellow"))
                for line in key_instructions(api_key_env, name):
                    console.print(Text("  " + line))

    # 3. Check the connection and choose a model.
    models: list[ModelInfo] = []
    can_connect = not api_key_env or lookup_api_key(name, api_key_env).found
    if can_connect:
        console.print(Text("Checking the connection...", style="dim"))
        for attempt in range(3):
            try:
                models = _list_models(name, entry)
                console.print(Text(f"Connected: {len(models)} models available.", style="green"))
                break
            except AuthenticationError as exc:
                console.print(Text(f"That key was rejected: {exc.message}", style="red"))
                if not api_key_env or attempt == 2:
                    break
                secret = ask_secret("Paste the API key again (Enter to skip): ").strip()
                if not secret:
                    break
                _store_key(name, api_key_env, secret, console)
            except ApexError as exc:
                console.print(Text(f"Could not list models: {exc.message}", style="yellow"))
                if exc.hint:
                    console.print(Text(exc.hint, style="dim"))
                break
    suggested = entry.get("model") or (preset.suggested_model if preset else None)
    if models:
        options = [(m.id, m.description or "") for m in chat_models(models, suggested)]
        model = pick(
            console,
            ask,
            options,
            title=f"Choose a model ({name})",
            default=suggested if any(m.id == suggested for m in models) else options[0][0],
            custom_label="type a model id",
        )
    else:
        if name == "ollama" and can_connect:
            console.print(Text("Tip: download a model first, e.g. `ollama pull llama3.2`.",
                               style="dim"))  # fmt: skip
        hint = f" (Enter for {suggested})" if suggested else ""
        model = ask(f"Model id{hint}: ").strip() or suggested
    if model:
        entry["model"] = model

    # 4. Save.
    providers[name] = entry
    data = dict(existing)
    data["providers"] = providers
    data["default_provider"] = name
    write_config_file(data)
    HistoryStore().initialize()
    if model:
        console.print(Text(f"All set: {name} / {model} is your default.", style="bold green"))
    else:
        console.print(
            Text(
                f"Saved {name}, but no model is chosen yet: run `apex model` when you know which.",
                style="yellow",
            )
        )
    return name


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
    """Set up a provider from flags (e.g. --provider ollama --model llama3.2); no flags = setup."""
    console = stdout_console()
    existing = read_config_file()
    if provider is None:
        if not stdin_is_interactive():
            console.print("Non-interactive: pass --provider (and usually --model).")
            raise typer.Exit(EXIT_USAGE)
        run_setup_wizard(console)
        return

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
    console.print(
        Text("Next: run `apex` to start chatting (`apex doctor` checks the setup).", style="bold")
    )


@guard
def setup() -> None:
    """Interactive setup: choose a provider, paste your API key, pick a model."""
    if not stdin_is_interactive():
        stdout_console().print(
            "Setup needs an interactive terminal (or use `apex init --provider`)."
        )
        raise typer.Exit(EXIT_USAGE)
    run_setup_wizard()


@guard
def model() -> None:
    """Choose the provider and model to use (interactive)."""
    if not stdin_is_interactive():
        stdout_console().print(
            "This needs an interactive terminal; otherwise use "
            "`apex config set providers.<name>.model <id>`."
        )
        raise typer.Exit(EXIT_USAGE)
    run_setup_wizard(welcome=False)


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
            else f"not available (optional: {KEYRING_INSTALL_HINT})",
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
