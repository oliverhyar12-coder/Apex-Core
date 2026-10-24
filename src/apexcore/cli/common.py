"""Helpers shared by CLI commands."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

import typer

from apexcore.cli.ui import print_error, stderr_console
from apexcore.errors import ApexError

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
