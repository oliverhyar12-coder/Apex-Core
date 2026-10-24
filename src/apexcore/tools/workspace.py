"""Workspace containment.

Every path a tool touches goes through :meth:`Workspace.resolve`, which

* resolves symlinks and ``..`` and rejects anything that ends up outside the
  workspace root (so ``../../etc/passwd`` and a symlink to ``~/.ssh`` both fail);
* refuses the ``.git`` directory (hooks and config there can execute code, and
  remote URLs may embed tokens);
* refuses files that look like credentials (``.env``, ``*.pem``, ``id_rsa``...);
* on Windows, refuses alternate data streams and reserved device names.

Limitation: checks happen immediately before each operation, but a *separate,
concurrently running* malicious process could still swap a path for a symlink
between the check and the use. APEX CORE does not defend against an attacker
who already runs code on your machine.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePath

from apexcore.errors import WorkspaceError

DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*.tfstate",
    "*.tfstate.*",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".netrc",
    "_netrc",
    ".pgpass",
    ".git-credentials",
    ".pypirc",
    ".npmrc",
    ".htpasswd",
    "credentials",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.toml",
    "service-account*.json",
)

# Template files that are meant to be shared.
SECRET_ALLOWLIST: tuple[str, ...] = (
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    "*.example.env",
)

DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        ".eggs",
        "target",
        ".idea",
        ".gradle",
    }
)

_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def _is_windows() -> bool:
    return os.name == "nt"


class Workspace:
    """A directory tree the agent may operate in."""

    def __init__(
        self,
        root: Path | str,
        *,
        extra_secret_patterns: Iterable[str] = (),
        extra_ignore_dirs: Iterable[str] = (),
    ) -> None:
        try:
            resolved = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(f"Workspace {root} does not exist or is not accessible.") from exc
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace {resolved} is not a directory.")
        self.root = resolved
        self.secret_patterns = tuple(p.lower() for p in DEFAULT_SECRET_PATTERNS) + tuple(
            p.lower() for p in extra_secret_patterns
        )
        self.ignore_dirs = DEFAULT_IGNORE_DIRS | frozenset(extra_ignore_dirs)

    # --- Path checks -----------------------------------------------------

    def _check_raw(self, raw: str) -> None:
        if "\x00" in raw:
            raise WorkspaceError("Path contains a NUL character.")
        if raw.startswith("~"):
            raise WorkspaceError(
                f"Path {raw!r} uses '~'. Use a path relative to the workspace root instead."
            )
        if _is_windows():
            pure = PurePath(raw)
            for part in pure.parts[1:] if pure.drive else pure.parts:
                if ":" in part:
                    raise WorkspaceError(f"Path {raw!r} uses an alternate data stream.")
                stem = part.split(".")[0].strip().lower()
                if stem in _WINDOWS_RESERVED:
                    raise WorkspaceError(f"Path {raw!r} uses a reserved Windows device name.")

    def resolve(self, raw: str | os.PathLike[str] = ".") -> Path:
        """Return the absolute, symlink-resolved path for ``raw`` inside the workspace."""
        text = os.fspath(raw).strip() or "."
        self._check_raw(text)
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:  # e.g. symlink loops
            raise WorkspaceError(f"Cannot resolve path {text!r}: {exc}") from exc
        if resolved != self.root and not resolved.is_relative_to(self.root):
            raise WorkspaceError(
                f"Path {text!r} resolves outside the workspace ({self.root}). "
                "Only files inside the workspace can be accessed."
            )
        rel_parts = [part.lower() for part in resolved.relative_to(self.root).parts]
        if ".git" in rel_parts:
            raise WorkspaceError(
                f"Access to {text!r} is blocked: the .git directory is off-limits "
                "(use the git_status / git_diff tools instead)."
            )
        return resolved

    def is_secret(self, path: Path) -> bool:
        name = path.name.lower()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in SECRET_ALLOWLIST):
            return False
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.secret_patterns)

    def check_not_secret(self, path: Path) -> None:
        if self.is_secret(path):
            raise WorkspaceError(
                f"{self.relative(path)} looks like a credentials file and is blocked to "
                "prevent secret exposure. (Configure tools.extra_secret_patterns to "
                "block more; the built-in list cannot be relaxed.)"
            )

    def relative(self, path: Path) -> str:
        """Workspace-relative path with forward slashes, for display and for the model."""
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return str(path)
        text = rel.as_posix()
        return text if text != "." else "."

    # --- Walking ---------------------------------------------------------

    def walk_files(self, start: Path) -> Iterator[Path]:
        """Yield files under ``start`` (already resolved), skipping ignored
        directories and never following directory symlinks."""
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if d not in self.ignore_dirs and d.lower() != ".git"
            )
            for filename in sorted(filenames):
                yield Path(dirpath) / filename
