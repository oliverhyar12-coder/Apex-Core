"""API-key lookup.

Keys are never stored in ``config.toml``. For a provider whose configuration
says ``api_key_env = "OPENAI_API_KEY"``, the key is looked up in order:

1. the ``OPENAI_API_KEY`` environment variable;
2. the credentials file ``<config dir>/.env`` (``OPENAI_API_KEY=...``);
3. the operating-system keyring, if the optional ``keyring`` package is
   installed (service ``apex-core``, username = provider name).

A ``.env`` file in the *current working directory* is never read: it belongs to
whatever project you are in, which APEX CORE does not trust.

Set ``APEX_NO_KEYRING=1`` to skip the keyring (useful on headless Linux where a
keyring backend may block waiting for an unlock prompt).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from dotenv import dotenv_values, set_key, unset_key

from apexcore import APP_NAME, paths
from apexcore.errors import ConfigError

KEYRING_SERVICE = APP_NAME
NO_KEYRING_ENV = "APEX_NO_KEYRING"


@dataclass(frozen=True)
class KeyLookup:
    value: str | None
    source: str | None  # "env", "file", "keyring", or None when not found

    @property
    def found(self) -> bool:
        return bool(self.value)


def _keyring() -> Any | None:
    if os.environ.get(NO_KEYRING_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return None
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    return keyring


def keyring_available() -> bool:
    module = _keyring()
    if module is None:
        return False
    try:
        backend = module.get_keyring()
    except Exception:
        return False
    # The "fail" backend means keyring is installed but has nowhere to store data.
    return "fail" not in type(backend).__module__.lower()


def _read_env_file(name: str) -> str | None:
    path = paths.env_file()
    if not path.is_file():
        return None
    try:
        values = dotenv_values(path, encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    value = values.get(name)
    return value.strip() if value else None


def lookup_api_key(provider_name: str, api_key_env: str | None) -> KeyLookup:
    """Find the API key for a provider without ever raising for a missing key."""
    if api_key_env:
        value = os.environ.get(api_key_env, "").strip()
        if value:
            return KeyLookup(value, "env")
        value = _read_env_file(api_key_env) or ""
        if value:
            return KeyLookup(value, "file")
    module = _keyring()
    if module is not None:
        try:
            value = module.get_password(KEYRING_SERVICE, provider_name) or ""
        except Exception:
            value = ""
        if value.strip():
            return KeyLookup(value.strip(), "keyring")
    return KeyLookup(None, None)


def describe_source(lookup: KeyLookup, api_key_env: str | None) -> str:
    if lookup.source == "env":
        return f"environment variable {api_key_env}"
    if lookup.source == "file":
        return f"credentials file ({paths.env_file()})"
    if lookup.source == "keyring":
        return "OS keyring"
    return "not found"


# How to add the optional keyring package, for either way of installing.
KEYRING_INSTALL_HINT = (
    "rerun the installer with --with-keyring (-WithKeyring on Windows), "
    "or: pipx inject apex-core-cli keyring"
)


def store_in_keyring(provider_name: str, value: str) -> None:
    module = _keyring()
    if module is None or not keyring_available():
        raise ConfigError(
            "No usable OS keyring.",
            hint=f"To add keyring support, {KEYRING_INSTALL_HINT}. Or use --env-file "
            "or an environment variable instead.",
        )
    try:
        module.set_password(KEYRING_SERVICE, provider_name, value)
    except Exception as exc:
        raise ConfigError(f"Could not store the key in the OS keyring: {exc}") from exc


def delete_from_keyring(provider_name: str) -> bool:
    module = _keyring()
    if module is None:
        return False
    try:
        module.delete_password(KEYRING_SERVICE, provider_name)
    except Exception:
        return False
    return True


def store_in_env_file(api_key_env: str, value: str) -> None:
    """Write ``NAME=value`` into the private credentials file in the config dir."""
    paths.ensure_private_dir(paths.config_dir())
    path = paths.env_file()
    if not path.exists():
        paths.atomic_write_text(
            path,
            "# APEX CORE credentials. Keep this file private; never commit it.\n",
            private=True,
        )
    set_key(path, api_key_env, value, quote_mode="always", encoding="utf-8")
    paths.restrict_file(path)


def delete_from_env_file(api_key_env: str) -> bool:
    path = paths.env_file()
    if not path.is_file():
        return False
    removed, _ = unset_key(path, api_key_env, encoding="utf-8")
    return bool(removed)
