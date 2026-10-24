"""The ``apex`` command-line application."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from typing import Annotated

import typer
from rich.text import Text

from apexcore import DIST_NAME, __version__
from apexcore.cli import commands_config, commands_history, commands_memory, commands_providers
from apexcore.cli.commands_agent import (
    AutoOpt,
    ContinueOpt,
    ModelOpt,
    NoToolsOpt,
    ProviderOpt,
    ReadOnlyOpt,
    ResumeOpt,
    WorkspaceOpt,
    ask,
    chat,
    start_chat,
    task,
    tools,
)
from apexcore.cli.commands_providers import models
from apexcore.cli.commands_setup import doctor, init, run_setup_wizard, setup
from apexcore.cli.commands_setup import model as model_command
from apexcore.cli.common import EXIT_INTERRUPTED, EXIT_USAGE, guard
from apexcore.cli.session import stdin_is_interactive
from apexcore.cli.ui import print_error, stderr_console, stdout_console
from apexcore.config import read_config_file
from apexcore.errors import ApexError, AuthenticationError, ConfigError

app = typer.Typer(
    name="apex",
    help="APEX CORE: a terminal AI agent. Bring your own API keys.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
    # Pretty tracebacks print local variables, which could include API keys.
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"apex {__version__} ({DIST_NAME})")
        raise typer.Exit()


def _interactive_terminal() -> bool:
    try:
        return stdin_is_interactive() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _needs_setup(exc: ApexError) -> bool:
    message = exc.message
    return isinstance(exc, ConfigError | AuthenticationError) and (
        "No provider configured" in message
        or "No model configured" in message
        or "No API key found" in message
    )


@app.callback()
@guard
def root(
    ctx: typer.Context,
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
    continue_last: ContinueOpt = False,
    resume: ResumeOpt = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    workspace: WorkspaceOpt = None,
    no_tools: NoToolsOpt = False,
    read_only: ReadOnlyOpt = False,
    auto: AutoOpt = False,
) -> None:
    """APEX CORE: a terminal AI agent. Bring your own API keys.

    Run `apex` to start chatting (first run walks you through setup).
    `apex -c` continues your last chat. `apex --help` lists everything else.
    """
    chat_options = (continue_last, resume, provider, model, workspace, no_tools, read_only, auto)
    if ctx.invoked_subcommand is not None:
        if any(chat_options):
            stderr_console().print(
                f"Options such as --model go after the command name: "
                f"`apex {ctx.invoked_subcommand} --help` lists the ones it takes. "
                "(`apex --model NAME` on its own starts a chat.)",
                style="yellow",
            )
            raise typer.Exit(EXIT_USAGE)
        return
    if not _interactive_terminal():
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    options = {
        "provider": provider,
        "model": model,
        "workspace": workspace,
        "no_tools": no_tools,
        "read_only": read_only,
        "resume": resume,
        "continue_last": continue_last,
        "auto": auto,
    }
    try:
        code = start_chat(**options)
    except ApexError as exc:
        if not _needs_setup(exc):
            raise
        stdout_console().print(Text(exc.message, style="yellow"))
        run_setup_wizard(welcome=not read_config_file())
        code = start_chat(**options)
    raise typer.Exit(code)


app.command(rich_help_panel="Setup")(setup)
app.command(rich_help_panel="Setup")(model_command)
app.command(rich_help_panel="Setup")(init)
app.command(rich_help_panel="Setup")(doctor)
app.add_typer(commands_config.app, name="config", rich_help_panel="Setup")
app.add_typer(commands_providers.app, name="providers", rich_help_panel="Setup")
app.command(rich_help_panel="Setup")(models)
app.command(rich_help_panel="Agent")(chat)
app.command(rich_help_panel="Agent")(ask)
app.command(rich_help_panel="Agent")(task)
app.command(rich_help_panel="Agent")(tools)
app.add_typer(commands_memory.app, name="memory", rich_help_panel="Agent")
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
