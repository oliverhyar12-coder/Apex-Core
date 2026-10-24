"""Per-user, per-platform locations for configuration and data.

Defaults come from :mod:`platformdirs`:

* Linux: configuration in ``$XDG_CONFIG_HOME/apex-core`` (usually
  ``~/.config/apex-core``), data in ``$XDG_DATA_HOME/apex-core`` (usually
  ``~/.local/share/apex-core``).
* macOS: both in ``~/Library/Application Support/apex-core``.
* Windows: both in ``%LOCALAPPDATA%\\apex-core``.

``APEX_CONFIG_DIR`` and ``APEX_DATA_DIR`` override them.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import platformdirs

from apexcore import APP_NAME

CONFIG_DIR_ENV = "APEX_CONFIG_DIR"
DATA_DIR_ENV = "APEX_DATA_DIR"

CONFIG_FILE_NAME = "config.toml"
ENV_FILE_NAME = ".env"
HISTORY_FILE_NAME = "history.sqlite3"


def _override(env_name: str) -> Path | None:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def config_dir() -> Path:
    """Directory holding ``config.toml`` and the optional credentials ``.env`` file."""
    return _override(CONFIG_DIR_ENV) or platformdirs.user_config_path(
        APP_NAME, appauthor=False, roaming=False
    )


def data_dir() -> Path:
    """Directory holding the SQLite history database."""
    return _override(DATA_DIR_ENV) or platformdirs.user_data_path(
        APP_NAME, appauthor=False, roaming=False
    )


def config_file() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def env_file() -> Path:
    """Optional dotenv file with API keys. Never read from the working directory."""
    return config_dir() / ENV_FILE_NAME


def history_db() -> Path:
    return data_dir() / HISTORY_FILE_NAME


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) and restrict it to the current user on POSIX.

    On Windows the per-user AppData directories already inherit an ACL that
    limits access to the user, so no change is made there.
    """
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            path.chmod(0o700)
    return path


def restrict_file(path: Path) -> None:
    """Make ``path`` readable and writable only by its owner (POSIX only)."""
    if os.name == "posix":
        with contextlib.suppress(OSError):
            path.chmod(0o600)


def atomic_write_text(path: Path, text: str, *, private: bool = False) -> None:
    """Write ``text`` as UTF-8 to ``path`` atomically.

    The data is written to a temporary file in the same directory, flushed to
    disk, then moved over the destination with :func:`os.replace`, so readers
    never observe a half-written file. Line endings are written exactly as given.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            restrict_file(tmp)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
