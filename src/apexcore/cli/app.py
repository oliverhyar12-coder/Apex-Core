"""The ``apex`` command-line application."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from typing import Annotated

import typer

from apexcore import DIST_NAME, __version__
from apexcore.cli import commands_config, commands_history, commands_providers
from apexcore.cli.commands_agent import ask, chat, task, tools
from apexcore.cli.commands_providers import models
from apexcore.cli.commands_setup import doctor, init
from apexcore.cli.common import EXIT_INTERRUPTED
from apexcore.cli.ui import print_error, stderr_console
from apexcore.errors import ApexError

app = typer.Typer(
    name="apex",
    help="APEX CORE: a terminal AI agent. Bring your own API keys.",
    no_args_is_help=True,
    add_completion=False,
    # Pretty tracebacks print local variables, which could include API keys.
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"apex {__version__} ({DIST_NAME})")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """APEX CORE: a terminal AI agent. Bring your own API keys.

    Start with `apex init`, check with `apex doctor`, then `apex chat`.
    """


app.command(rich_help_panel="Setup")(init)
app.command(rich_help_panel="Setup")(doctor)
app.add_typer(commands_config.app, name="config", rich_help_panel="Setup")
app.add_typer(commands_providers.app, name="providers", rich_help_panel="Setup")
app.command(rich_help_panel="Setup")(models)
app.command(rich_help_panel="Agent")(chat)
app.command(rich_help_panel="Agent")(ask)
app.command(rich_help_panel="Agent")(task)
app.command(rich_help_panel="Agent")(tools)
app.add_typer(commands_history.app, name="history", rich_help_panel="History")


def _configure_stdio() -> None:
    # Never crash on characters the terminal encoding cannot represent
    # (e.g. a redirected stdout using cp1252 on Windows).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="replace")


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _raise_terminate(signum: int, frame: object) -> None:
    # SystemExit unwinds the stack, so child processes are killed in finally blocks.
    raise SystemExit(128 + signum)


def _install_signal_handlers() -> None:
    with contextlib.suppress(ValueError, OSError, AttributeError):
        if hasattr(signal, "SIGBREAK"):  # Windows: treat Ctrl+Break like Ctrl+C
            signal.signal(signal.SIGBREAK, _raise_interrupt)
        if os.name == "posix":
            signal.signal(signal.SIGTERM, _raise_terminate)


def main() -> None:
    """Console-script entry point."""
    _configure_stdio()
    _install_signal_handlers()
    try:
        # Typer handles --help, usage errors and exit codes; commands handle
        # their own errors via apexcore.cli.common.guard.
        app(prog_name="apex")
    except KeyboardInterrupt:
        stderr_console().print("\nCancelled.", style="yellow")
        sys.exit(EXIT_INTERRUPTED)
    except ApexError as exc:
        print_error(exc)
        sys.exit(exc.exit_code)
    except BrokenPipeError:
        # Output was piped into a program that exited early (e.g. `| head`).
        with contextlib.suppress(OSError):
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)
