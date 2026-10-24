from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from apexcore import paths
from apexcore.config import (
    ProviderConfig,
    load_settings,
    parse_value,
    read_config_file,
    set_config_value,
    unset_config_value,
    write_config_file,
)
from apexcore.errors import ConfigError
from apexcore.providers.factory import provider_config, require_model, resolve_provider
from apexcore.providers.presets import PRESETS
from conftest import write_config


def test_paths_honour_overrides(tmp_path: Path) -> None:
    assert paths.config_dir() == (tmp_path / "apex-config").resolve()
    assert paths.data_dir() == (tmp_path / "apex-data").resolve()
    assert paths.config_file().name == "config.toml"
    assert paths.history_db().parent == paths.data_dir()
    assert paths.env_file().parent == paths.config_dir()


def test_default_paths_are_per_user_and_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APEX_CONFIG_DIR")
    monkeypatch.delenv("APEX_DATA_DIR")
    config_dir, data_dir = paths.config_dir(), paths.data_dir()
    assert config_dir.is_absolute() and data_dir.is_absolute()
    assert "apex-core" in config_dir.parts
    if sys.platform == "win32":
        assert "AppData" in config_dir.parts
    elif sys.platform == "darwin":
        assert "Application Support" in config_dir.parts
    else:
        assert str(config_dir).startswith(os.environ.get("XDG_CONFIG_HOME", str(Path.home())))


def test_relative_override_is_made_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APEX_DATA_DIR", "relative-data")
    assert paths.data_dir() == (tmp_path / "relative-data").resolve()


def test_defaults_without_config_file() -> None:
    settings = load_settings()
    assert settings.default_provider is None
    assert settings.providers == {}
    assert settings.permissions.write == "ask"
    assert settings.permissions.execute == "ask"
    assert settings.agent.max_steps == 12


def test_toml_and_env_layering(monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(
        """
default_provider = "local"
[providers.local]
base_url = "http://localhost:9999/v1/"
model = "m1"
[agent]
max_steps = 5
"""
    )
    settings = load_settings()
    assert settings.providers["local"].base_url == "http://localhost:9999/v1"
    assert settings.agent.max_steps == 5
    monkeypatch.setenv("APEX_AGENT__MAX_STEPS", "7")
    monkeypatch.setenv("APEX_PROVIDERS__LOCAL__MODEL", "m2")
    settings = load_settings()
    assert settings.agent.max_steps == 7
    assert settings.providers["local"].model == "m2"
    assert settings.providers["local"].base_url == "http://localhost:9999/v1"


def test_invalid_toml_is_a_config_error() -> None:
    write_config("default_provider = [unclosed")
    with pytest.raises(ConfigError, match="Could not parse"):
        load_settings()


def test_validation_errors_are_readable() -> None:
    write_config("[agent]\nmax_steps = 0\nbogus = 1\n")
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "agent.max_steps" in info.value.message
    assert "agent.bogus: unknown setting" in info.value.message


def test_api_key_in_config_file_is_rejected() -> None:
    write_config('[providers.x]\nbase_url = "https://x.test/v1"\napi_key = "sk-secretsecret"\n')
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "API keys are not stored" in info.value.message
    assert "sk-secretsecret" not in info.value.message


def test_api_key_env_must_be_a_name_not_a_key() -> None:
    with pytest.raises(ValueError, match="NAME of an environment variable"):
        ProviderConfig(base_url="https://x.test", api_key_env="sk-abc-def-123")


def test_base_url_must_be_http() -> None:
    with pytest.raises(ValueError):
        ProviderConfig(base_url="file:///etc/passwd")


def test_set_get_unset_roundtrip() -> None:
    write_config_file({"providers": {"local": {"base_url": "http://localhost:1/v1"}}})
    assert set_config_value("agent.max_steps", "30") == 30
    assert set_config_value("permissions.write", "deny") == "deny"
    assert set_config_value("providers.local.model", "4") == "4"  # string fallback
    assert set_config_value("permissions.auto_approve_commands", '[["git", "status"]]') == [
        ["git", "status"]
    ]
    data = read_config_file()
    assert data["agent"]["max_steps"] == 30
    assert data["providers"]["local"]["model"] == "4"
    assert unset_config_value("agent.max_steps") is True
    assert unset_config_value("agent.max_steps") is False
    assert "max_steps" not in read_config_file().get("agent", {})


def test_set_rejects_invalid_values_and_keeps_file() -> None:
    write_config_file({"agent": {"max_steps": 3}})
    with pytest.raises(ConfigError):
        set_config_value("agent.max_steps", "-1")
    with pytest.raises(ConfigError):
        set_config_value("providers.openai.api_key", "sk-live-1234567890")
    assert read_config_file() == {"agent": {"max_steps": 3}}


def test_parse_value() -> None:
    assert parse_value("true") is True
    assert parse_value("1.5") == 1.5
    assert parse_value('"quoted"') == "quoted"
    assert parse_value("bare-word") == "bare-word"


def test_config_file_written_atomically_and_privately() -> None:
    write_config_file({"default_provider": "openai"})
    path = paths.config_file()
    text = path.read_text(encoding="utf-8")
    assert 'default_provider = "openai"' in text
    leftovers = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_config_file_is_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    write_config_file({"providers": {"local": {"base_url": "http://x.test", "model": "modèle-ü"}}})
    raw = paths.config_file().read_bytes()
    assert "modèle-ü".encode() in raw
    assert load_settings().providers["local"].model == "modèle-ü"


def test_resolve_provider_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(
        'default_provider = "a"\n'
        '[providers.a]\nbase_url = "http://a.test"\nmodel = "ma"\n'
        '[providers.b]\nbase_url = "http://b.test"\nmodel = "mb"\n'
    )
    settings = load_settings()
    assert resolve_provider(settings).name == "a"
    monkeypatch.setenv("APEX_PROVIDER", "b")
    assert resolve_provider(settings).model == "mb"
    assert resolve_provider(settings, "a", "override").model == "override"
    monkeypatch.setenv("APEX_MODEL", "env-model")
    assert resolve_provider(settings, "a").model == "env-model"


def test_preset_usable_without_config() -> None:
    settings = load_settings()
    cfg = provider_config(settings, "ollama")
    assert cfg.base_url == PRESETS["ollama"].base_url
    assert cfg.api_key_env is None
    resolved = resolve_provider(settings, "ollama")
    with pytest.raises(ConfigError, match="No model configured"):
        require_model(resolved)


def test_unknown_provider_and_missing_default() -> None:
    settings = load_settings()
    with pytest.raises(ConfigError, match="No provider configured"):
        resolve_provider(settings)
    with pytest.raises(ConfigError, match="Unknown provider"):
        resolve_provider(settings, "nope")


def test_cwd_dotenv_is_not_loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("APEX_DEFAULT_PROVIDER=evil\n", encoding="utf-8")
    monkeypatch.chdir(project)
    assert load_settings().default_provider is None
