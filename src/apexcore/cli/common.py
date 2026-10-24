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


def editor_argv(path: Path) -> list[str]:
    """The command that opens ``path`` in $VISUAL / $EDITOR (default: Notepad on
    Windows, else nano/vim/vi).

    The variable may hold a program with arguments (``code --wait``), a quoted path
    (``"C:\\Program Files\\Notepad++\\notepad++.exe"``), or an unquoted absolute
    path with spaces. Bare program names are looked up on PATH without the current
    directory, so a repository cannot ship its own "editor".
    """
    editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
    if not editor:
        names = ("notepad",) if IS_WINDOWS else ("nano", "vim", "vi")
        found = next((p for p in map(which_no_cwd, names) if p is not None), None)
        if found is None and IS_WINDOWS:
            system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            found = Path(system_root) / "System32" / "notepad.exe"
        if found is None:
            raise ConfigError("No text editor found.", hint="Set the EDITOR environment variable.")
        return [str(found), str(path)]
    if Path(editor).is_absolute() and Path(editor).is_file():
        parts = [editor]
    else:
        parts = shlex.split(editor, posix=not IS_WINDOWS)
        if IS_WINDOWS:
            # Non-POSIX splitting keeps the quotes around "C:\Program Files\...".
            parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    if not parts or not parts[0]:
        raise ConfigError(f"Cannot use EDITOR={editor!r}.", hint="Set it to your editor's path.")
    program = parts[0]
    separators = ("/", "\\") if IS_WINDOWS else ("/",)
    if not any(sep in program for sep in separators):
        found = which_no_cwd(program)
        if found is None:
            raise ConfigError(
                f"The editor {program!r} was not found on PATH.",
                hint="Set EDITOR to the editor's full path.",
            )
        program = str(found)
    return [program, *parts[1:], str(path)]


def open_in_editor(path: Path) -> None:
    """Open ``path`` in the user's editor and wait for it to close."""
    argv = editor_argv(path)
    try:
        subprocess.run(argv, check=False)
    except OSError as exc:
        raise ConfigError(f"Could not start the editor {argv[0]!r}: {exc}") from exc
