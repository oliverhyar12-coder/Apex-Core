"""Configuration model, loading, and editing.

Settings are read, lowest priority first, from:

1. built-in defaults,
2. ``config.toml`` in the per-user configuration directory,
3. environment variables prefixed with ``APEX_`` (nested keys use ``__``, for
   example ``APEX_AGENT__MAX_STEPS=20``).

Command-line options override all of these for a single invocation.

API keys are deliberately *not* part of this model: the configuration only
names the environment variable that holds a key (``api_key_env``). See
:mod:`apexcore.secrets`.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from apexcore import paths
from apexcore.errors import ConfigError
from apexcore.providers.presets import ADAPTERS, OPENAI_COMPATIBLE, ProviderPreset

PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,39}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Pricing(_Strict):
    """Optional per-million-token prices, used only to estimate costs locally."""

    input_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float | None = Field(default=None, ge=0)
    currency: str = "USD"


class ProviderConfig(_Strict):
    adapter: str = OPENAI_COMPATIBLE
    base_url: str
    api_key_env: str | None = None
    model: str | None = None
    timeout: float = Field(default=60.0, gt=0, le=900)
    max_retries: int = Field(default=2, ge=0, le=6)
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    stream: bool = True
    supports_tools: bool = True
    # The model's context window in tokens, for the chat's context bar (optional).
    context_window: int | None = Field(default=None, ge=1000)
    pricing: Pricing = Field(default_factory=Pricing)

    @field_validator("adapter")
    @classmethod
    def _check_adapter(cls, value: str) -> str:
        if value not in ADAPTERS:
            raise ValueError(f"unknown adapter {value!r}; expected one of {', '.join(ADAPTERS)}")
        return value

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+", value):
            raise ValueError("base_url must start with http:// or https://")
        return value

    @field_validator("api_key_env")
    @classmethod
    def _check_env_name(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not ENV_NAME_RE.match(value):
            raise ValueError(
                "api_key_env must be the NAME of an environment variable "
                "(letters, digits, underscores), not the key itself"
            )
        return value

    @classmethod
    def from_preset(cls, preset: ProviderPreset, **overrides: Any) -> ProviderConfig:
        data: dict[str, Any] = {
            "adapter": preset.adapter,
            "base_url": preset.base_url,
            "api_key_env": preset.api_key_env,
            "model": preset.suggested_model,
        }
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**data)


class AgentSettings(_Strict):
    max_steps: int = Field(default=12, ge=1, le=100)
    # Personality overlay: a built-in name (see `/personality`) or one of `personalities`.
    personality: str | None = None
    personalities: dict[str, str] = Field(default_factory=dict)
    # Load APEX.md / AGENTS.md / CLAUDE.md / .cursorrules from the workspace root.
    context_files: bool = True


class PermissionSettings(_Strict):
    # File edits: ask before each change (default), allow without asking, or deny.
    write: Literal["ask", "allow", "deny"] = "ask"
    # Commands: ask before each run (default) or deny entirely. There is
    # intentionally no blanket "allow"; use auto_approve_commands for specific,
    # trusted argv prefixes such as ["git", "status"].
    execute: Literal["ask", "deny"] = "ask"
    # The agent's own memory notes (never workspace files): allow, ask, or deny.
    memory: Literal["allow", "ask", "deny"] = "allow"
    # Argv prefixes that run without asking (["git", "status"] also covers
    # "git status --short").
    auto_approve_commands: list[list[str]] = Field(default_factory=list)
    # Exact commands you chose to allow "always" at the prompt (no prefix matching).
    always_allowed_commands: list[list[str]] = Field(default_factory=list)
    allow_elevated: bool = False

    @field_validator("auto_approve_commands", "always_allowed_commands")
    @classmethod
    def _check_prefixes(cls, value: list[list[str]]) -> list[list[str]]:
        for prefix in value:
            if not prefix or any(not part for part in prefix):
                raise ValueError("each auto-approved command must be a non-empty argv list")
        return value


class ToolSettings(_Strict):
    command_timeout: float = Field(default=120.0, gt=0, le=3600)
    max_output_chars: int = Field(default=20_000, ge=1_000, le=500_000)
    max_read_bytes: int = Field(default=1_000_000, ge=1_000, le=50_000_000)
    extra_secret_patterns: list[str] = Field(default_factory=list)
    extra_ignore_dirs: list[str] = Field(default_factory=list)


class HistorySettings(_Strict):
    enabled: bool = True
    # Remember what you typed in chat (for the up-arrow), stored privately and redacted.
    save_input: bool = True


class MemorySettings(_Strict):
    enabled: bool = True


class UISettings(_Strict):
    # Render answers as Markdown in interactive chat (pipes always get plain text).
    markdown: bool = True
    # Use emoji in the tool activity feed (plain symbols when off or unsupported).
    emoji: bool = True


class Settings(BaseSettings):
    """Effective settings: defaults < config.toml < APEX_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="APEX_",
        env_nested_delimiter="__",
        extra="forbid",
        validate_assignment=True,
    )

    default_provider: str | None = None
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    ui: UISettings = Field(default_factory=UISettings)

    @field_validator("providers")
    @classmethod
    def _check_provider_names(cls, value: dict[str, ProviderConfig]) -> dict[str, ProviderConfig]:
        for name in value:
            if not PROVIDER_NAME_RE.match(name):
                raise ValueError(
                    f"invalid provider name {name!r}: use lowercase letters, digits, '-' or '_'"
                )
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Deliberately no dotenv source: a .env file in the working directory
        # belongs to whatever repository you are in and must not be able to
        # redirect your API traffic.
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=paths.config_file()),
        )


class _FileSettings(Settings):
    """Validates config-file data on its own, ignoring the environment."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "(root)"
        message = err["msg"]
        if err["type"] == "extra_forbidden":
            message = "unknown setting"
            if location.endswith(".api_key") or location == "api_key":
                message = "API keys are not stored in config.toml"
        lines.append(f"  {location}: {message}")
    return "\n".join(lines)


def _api_key_hint() -> str:
    return (
        "Keep API keys in an environment variable named by `api_key_env`, in the "
        f"credentials file {paths.env_file()}, or in the OS keyring (`apex config set-key`)."
    )


def load_settings() -> Settings:
    """Load the effective settings, raising :class:`ConfigError` with a readable message."""
    path = paths.config_file()
    try:
        return Settings()
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"Could not parse {path}: {exc}",
            hint="Fix the TOML syntax, or move the file away and run `apex init`.",
        ) from exc
    except ValidationError as exc:
        text = _format_validation_error(exc)
        hint = _api_key_hint() if "API keys" in text else "Check the file and APEX_* variables."
        raise ConfigError(
            f"Invalid configuration ({path} or APEX_* variables):\n{text}", hint=hint
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc


def read_config_file() -> dict[str, Any]:
    """Return the raw contents of ``config.toml`` (empty if it does not exist)."""
    path = paths.config_file()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc


def validate_config_data(data: dict[str, Any]) -> Settings:
    try:
        return _FileSettings(**data)
    except ValidationError as exc:
        text = _format_validation_error(exc)
        hint = _api_key_hint() if "API keys" in text else None
        raise ConfigError(f"Invalid configuration:\n{text}", hint=hint) from None


def write_config_file(data: dict[str, Any]) -> None:
    """Validate ``data`` and atomically write it to ``config.toml``."""
    validate_config_data(data)
    paths.ensure_private_dir(paths.config_dir())
    header = (
        "# APEX CORE configuration. Managed by `apex init` / `apex config set`;\n"
        "# hand edits are fine, but comments are not preserved when apex rewrites it.\n"
        "# API keys do not belong here: see `apex config path` and the README.\n\n"
    )
    paths.atomic_write_text(paths.config_file(), header + tomli_w.dumps(data), private=True)


# --- Dotted-key editing (`apex config get/set/unset`) ----------------------


def _split_key(key: str) -> list[str]:
    parts = key.strip().split(".")
    if not key.strip() or any(not part for part in parts):
        raise ConfigError(f"Invalid key {key!r}. Use dotted keys such as agent.max_steps.")
    return parts


def get_dotted(data: dict[str, Any], key: str) -> Any:
    node: Any = data
    for part in _split_key(key):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(key)
        node = node[part]
    return node


def parse_value(raw: str) -> Any:
    """Interpret a command-line value as TOML (numbers, booleans, arrays, quoted
    strings), falling back to a plain string."""
    try:
        return tomllib.loads(f"value = {raw}")["value"]
    except tomllib.TOMLDecodeError:
        return raw


def set_dotted(data: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    parts = _split_key(key)
    node = data
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ConfigError(f"Cannot set {key!r}: {part!r} is not a table.")
        node = child
    node[parts[-1]] = value
    return data


def unset_dotted(data: dict[str, Any], key: str) -> bool:
    parts = _split_key(key)
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]
        return True
    return False


def set_config_value(key: str, raw_value: str) -> Any:
    """Set ``key`` in config.toml from a command-line string; returns the stored value.

    The value is first interpreted as TOML; if that does not validate (for
    example a model called ``4``), it is retried as a plain string.
    """
    if _split_key(key)[-1] == "api_key":
        raise ConfigError("API keys are not stored in config.toml.", hint=_api_key_hint())
    typed = parse_value(raw_value)
    candidates = [typed] if typed == raw_value else [typed, raw_value]
    first_error: ConfigError | None = None
    for candidate in candidates:
        data = read_config_file()
        set_dotted(data, key, candidate)
        try:
            write_config_file(data)
        except ConfigError as exc:
            first_error = first_error or exc
            continue
        return candidate
    assert first_error is not None
    raise first_error


def unset_config_value(key: str) -> bool:
    data = read_config_file()
    removed = unset_dotted(data, key)
    if removed:
        write_config_file(data)
    return removed
