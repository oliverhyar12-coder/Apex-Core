"""``!command`` in chat: run something yourself, without a model turn.

The command is typed by you, so it runs in your normal shell (``$SHELL -c`` on
Linux and macOS, PowerShell on Windows) with your environment, in the
workspace directory. stdin is closed, so interactive programs will not work;
output is shown to you but not sent to the model.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console
from rich.text import Text

from apexcore.cli.ui import format_duration, sanitize
from apexcore.platform_utils import IS_WINDOWS, which_no_cwd
from apexcore.tools.process import run_process

MAX_SHELL_OUTPUT = 400_000


def shell_argv(command: str) -> list[str]:
    if IS_WINDOWS:
        # Never search the current directory: a repository could ship powershell.exe.
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        fallback = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        exe = which_no_cwd("pwsh") or which_no_cwd("powershell") or fallback
        return [str(exe), "-NoLogo", "-NoProfile", "-Command", command]
    shell = os.environ.get("SHELL", "")
    if not shell or not Path(shell).is_absolute() or not os.access(shell, os.X_OK):
        shell = "/bin/sh"
    return [shell, "-c", command]


def run_user_shell(command: str, *, cwd: Path, timeout: float, console: Console) -> int | None:
    """Run ``command`` and print its output. Returns the exit code (None on timeout)."""
    command = command.strip()
    if not command:
        console.print(Text("Usage: !<command>, for example !git status", style="yellow"))
        return None
    first = command.split()[0].lower()
    if first in ("sudo", "su", "doas", "runas"):
        console.print(
            Text(
                "Privileged commands need a password prompt, which !commands cannot show. "
                "Run it in your own terminal.",
                style="yellow",
            )
        )
        return None
    result = run_process(
        shell_argv(command), cwd=cwd, timeout=timeout, max_output_bytes=MAX_SHELL_OUTPUT
    )
    for text, style in ((result.stdout, ""), (result.stderr, "red")):
        if text.strip():
            console.print(Text(sanitize(text.rstrip("\n")), style=style))
    if result.timed_out:
        console.print(Text(f"(timed out after {timeout:.0f}s)", style="yellow"))
        return None
    status = "ok" if result.returncode == 0 else f"exit {result.returncode}"
    console.print(Text(f"({status}, {format_duration(result.duration)})", style="dim"))
    return result.returncode
