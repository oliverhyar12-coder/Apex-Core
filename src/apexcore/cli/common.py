"""Helpers shared by CLI commands."""

from __future__ import annotations

import functools
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import typer

from apexcore.cli.ui import print_error, stderr_console
from apexcore.errors import ApexError, ConfigError
from apexcore.platform_utils import IS_WINDOWS, which_no_cwd

F = TypeVar("F", bound=Callable[..., Any])

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


def guard(func: F) -> F:
    """Turn APEX CORE errors into a readable message and exit code, and Ctrl+C
    into exit code 130, instead of a traceback."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ApexError as exc:
            print_error(exc)
            raise typer.Exit(exc.exit_code) from None
        except (KeyboardInterrupt, typer.Abort):
            # typer.Abort is what an interrupted prompt raises.
            stderr_console().print("\nCancelled.", style="yellow")
            raise typer.Exit(EXIT_INTERRUPTED) from None

    return wrapper  # type: ignore[return-value]


def open_in_editor(path: Path) -> None:
    """Open ``path`` in $VISUAL / $EDITOR (Notepad on Windows, else nano/vim/vi)."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        argv = [*shlex.split(editor, posix=not IS_WINDOWS), str(path)]
    elif IS_WINDOWS:
        argv = ["notepad.exe", str(path)]
    else:
        found = next(
            (which_no_cwd(name) for name in ("nano", "vim", "vi") if which_no_cwd(name)), None
        )
        if found is None:
            raise ConfigError("No text editor found.", hint="Set the EDITOR environment variable.")
        argv = [str(found), str(path)]
    try:
        subprocess.run(argv, check=False)
    except OSError as exc:
        raise ConfigError(f"Could not start the editor {argv[0]!r}: {exc}") from exc
