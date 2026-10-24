"""Build provider instances from configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from apexcore.config import ProviderConfig, Settings
from apexcore.errors import AuthenticationError, ConfigError
from apexcore.providers.anthropic import AnthropicProvider
from apexcore.providers.base import Provider
from apexcore.providers.openai_compat import OpenAICompatibleProvider
from apexcore.providers.presets import ANTHROPIC, PRESETS, get_preset
from apexcore.redaction import GLOBAL_REDACTOR, Redactor
from apexcore.secrets import lookup_api_key

PROVIDER_ENV = "APEX_PROVIDER"
MODEL_ENV = "APEX_MODEL"


@dataclass
class ResolvedProvider:
    name: str
    config: ProviderConfig
    model: str | None


def provider_config(settings: Settings, name: str) -> ProviderConfig:
    """Configuration for ``name``: the configured entry, else the preset of that name."""
    if name in settings.providers:
        return settings.providers[name]
    preset = get_preset(name)
    if preset is not None:
        return ProviderConfig.from_preset(preset)
    known = sorted(set(settings.providers) | set(PRESETS))
    raise ConfigError(
        f"Unknown provider {name!r}.",
        hint=f"Known providers: {', '.join(known)}. Add one with `apex providers add`.",
    )


def resolve_provider(
    settings: Settings, provider: str | None = None, model: str | None = None
) -> ResolvedProvider:
    """Pick the provider and model: CLI option > APEX_PROVIDER/APEX_MODEL > config."""
    name = provider or os.environ.get(PROVIDER_ENV, "").strip() or settings.default_provider or ""
    if not name:
        raise ConfigError(
            "No provider configured.",
            hint="Run `apex init` to choose a provider, or pass --provider.",
        )
    config = provider_config(settings, name)
    chosen = model or os.environ.get(MODEL_ENV, "").strip() or config.model
    return ResolvedProvider(name=name, config=config, model=chosen or None)


def require_model(resolved: ResolvedProvider) -> str:
    if not resolved.model:
        raise ConfigError(
            f"No model configured for provider {resolved.name!r}.",
            hint=f"Pass --model, or run `apex models --provider {resolved.name}` and then "
            f"`apex config set providers.{resolved.name}.model <model>`.",
        )
    return resolved.model


def create_provider(
    name: str,
    config: ProviderConfig,
    *,
    redactor: Redactor | None = None,
    require_key: bool = True,
) -> Provider:
    """Instantiate the adapter for ``config``, loading its API key."""
    redactor = redactor or GLOBAL_REDACTOR
    api_key: str | None = None
    if config.api_key_env:
        lookup = lookup_api_key(name, config.api_key_env)
        api_key = lookup.value
        if not api_key and require_key:
            raise AuthenticationError(
                f"No API key found for provider {name!r}.",
                hint=f"Set the {config.api_key_env} environment variable, or run "
                f"`apex config set-key {name}`. See `apex doctor`.",
            )
        redactor.add(api_key)
        GLOBAL_REDACTOR.add(api_key)
    cls = AnthropicProvider if config.adapter == ANTHROPIC else OpenAICompatibleProvider
    return cls(
        name=name,
        base_url=config.base_url,
        api_key=api_key,
        api_key_env=config.api_key_env,
        timeout=config.timeout,
        max_retries=config.max_retries,
        stream=config.stream,
        supports_tools=config.supports_tools,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        redactor=redactor,
    )
