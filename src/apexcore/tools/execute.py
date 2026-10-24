"""The run_command tool: approval-gated, argv-only command execution.

Commands are given as an argument list and run *without a shell*, so pipes,
redirection, globbing and ``&&`` are not interpreted. Each run has a timeout,
an output cap, stdin closed, a working directory inside the workspace, and an
environment with credential-like variables removed.

This is not a sandbox. An approved command runs with your user's full
privileges and can do anything you could, which includes running the
repository's own code (``pytest`` imports ``conftest.py``; ``npm test`` runs
package scripts). Approve commands as you would type them yourself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field

from apexcore.errors import CommandBlockedError, ToolError, WorkspaceError
from apexcore.platform_utils import IS_WINDOWS, format_argv, is_elevated, which_no_cwd
from apexcore.tools.base import (
    PreparedAction,
    Risk,
    Tool,
    ToolArgs,
    ToolContext,
    ToolResult,
    truncate,
)
from apexcore.tools.process import run_process, scrubbed_env

MAX_TIMEOUT = 3600.0

# Never run, even with approval.
BLOCKED_PROGRAMS = frozenset(
    {
        # privilege escalation
        "sudo", "su", "doas", "pkexec", "runas", "gsudo", "sudo-rs",
        # disks and filesystems
        "mkfs", "dd", "format", "diskpart", "fdisk", "sfdisk", "gdisk", "parted",
        "wipefs", "diskutil", "bcdedit", "vssadmin",
        # power state
        "shutdown", "reboot", "halt", "poweroff", "init", "telinit",
    }
)  # fmt: skip

# Run only after explicit approval and never auto-approved.
DESTRUCTIVE_PROGRAMS = frozenset(
    {
        "rm", "rmdir", "del", "erase", "rd", "remove-item", "shred", "unlink", "truncate",
        "mv", "move", "chmod", "chown", "chgrp", "icacls", "takeown", "attrib",
        "kill", "killall", "pkill", "taskkill", "stop-process",
        "curl", "wget", "invoke-webrequest", "iwr", "scp", "rsync", "ssh", "ftp",
    }
)  # fmt: skip

_DESTRUCTIVE_GIT = {
    "clean": None,
    "reset": {"--hard", "--merge", "--keep"},
    "push": {"-f", "--force", "--force-with-lease", "--delete", "-d", "--mirror"},
    "checkout": {"-f", "--force", "--", "."},
    "restore": None,
    "rm": None,
    "branch": {"-d", "--delete", "-m"},
    "stash": {"drop", "clear"},
    "filter-branch": None,
    "filter-repo": None,
    "gc": {"--prune=now", "--aggressive"},
    "reflog": {"expire", "delete"},
    "update-ref": {"-d"},
}

_SHELLS = frozenset(
    {"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh"}
)
_INTERPRETERS = {
    "python": {"-c"},
    "python3": {"-c"},
    "py": {"-c"},
    "node": {"-e", "--eval", "-p", "--print"},
    "deno": {"eval"},
    "perl": {"-e", "-E"},
    "ruby": {"-e"},
    "php": {"-r"},
}
# Characters cmd.exe interprets even inside an argument passed to a .bat/.cmd.
_BATCH_UNSAFE = re.compile(r'[&|<>^%!"()\r\n]')


def program_name(argv0: str) -> str:
    name = Path(argv0.replace("\\", "/")).name.lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


@dataclass
class CommandAssessment:
    blocked_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    auto_approvable: bool = True


def assess_command(argv: list[str]) -> CommandAssessment:
    """Classify a command line. Pure function, easy to test on every OS."""
    result = CommandAssessment()
    prog = program_name(argv[0])
    lowered = [arg.lower() for arg in argv[1:]]
    if prog in BLOCKED_PROGRAMS or prog.startswith("mkfs."):
        result.blocked_reason = (
            f"'{prog}' is blocked: APEX CORE never runs privilege-escalation, "
            "disk-formatting, or shutdown commands. Run it yourself if you really need it."
        )
        return result
    if prog in DESTRUCTIVE_PROGRAMS:
        result.warnings.append(f"'{prog}' can delete or alter data, or move data off this machine.")
        result.auto_approvable = False
    if prog == "git":
        # Arguments are compared lower-cased, so the rule sets are lower-case too.
        for sub in (a for a in lowered if not a.startswith("-")):
            if sub not in _DESTRUCTIVE_GIT:
                continue
            rule = _DESTRUCTIVE_GIT[sub]
            if rule is None or any(a in rule for a in lowered):
                result.warnings.append(f"'git {sub}' can discard work or rewrite history.")
                result.auto_approvable = False
                break
    if prog == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in lowered):
        result.warnings.append("'find' with -delete/-exec can modify or remove files.")
        result.auto_approvable = False
    if prog in _SHELLS:
        result.warnings.append(
            f"'{prog}' runs shell code whose effects cannot be checked here; review it carefully."
        )
        result.auto_approvable = False
    inline = _INTERPRETERS.get(prog)
    if inline and any(a in inline for a in argv[1:]):
        result.warnings.append(f"'{prog}' will execute inline code; review it carefully.")
        result.auto_approvable = False
    if any("rm -rf" in a or "format c:" in a for a in lowered):
        result.warnings.append("An argument contains a destructive shell command.")
        result.auto_approvable = False
    return result


def resolve_executable(argv0: str, cwd: Path, ctx: ToolContext) -> Path:
    """Find the program to run without letting the workspace shadow PATH entries."""
    if "/" in argv0 or "\\" in argv0:
        # An explicit path (./gradlew, .venv/bin/python, /usr/bin/python3).
        candidate = Path(argv0)
        if not candidate.is_absolute():
            try:
                candidate = ctx.workspace.resolve(cwd / candidate)
            except WorkspaceError as exc:
                raise CommandBlockedError(str(exc)) from exc
        if not candidate.is_file():
            raise ToolError(f"Executable {argv0!r} not found.")
        return candidate
    found = which_no_cwd(argv0)
    if found is None:
        hint = (
            " On Windows, shell built-ins (dir, type, echo) are not programs." if IS_WINDOWS else ""
        )
        raise ToolError(f"Program {argv0!r} was not found on PATH.{hint}")
    return found


class RunCommandArgs(ToolArgs):
    argv: list[str] = Field(
        min_length=1,
        description='Program and arguments as a list, e.g. ["python", "-m", "pytest", "-q"]. '
        "No shell: pipes, redirects, globs and && are not supported.",
    )
    cwd: str = Field(default=".", description="Working directory relative to the workspace root.")
    timeout: float = Field(
        default=0, ge=0, description="Seconds before the command is killed (0 = default)."
    )


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run a program in the workspace (for example tests, linters, or build steps). "
        "Arguments are an argv list executed without a shell. Every command requires "
        "user approval unless the user pre-approved it. Output is truncated."
    )
    risk = Risk.EXECUTE
    Args = RunCommandArgs

    def prepare(self, args: RunCommandArgs, ctx: ToolContext) -> PreparedAction:
        argv = list(args.argv)
        if any("\x00" in part for part in argv) or not argv[0].strip():
            raise ToolError("argv contains an empty program name or a NUL character.")
        if is_elevated() and not ctx.permission_settings.allow_elevated:
            raise CommandBlockedError(
                "APEX CORE is running as root/Administrator, so command execution is "
                "disabled. Run apex as a normal user (or set permissions.allow_elevated "
                "if you understand the risk)."
            )
        assessment = assess_command(argv)
        if assessment.blocked_reason:
            raise CommandBlockedError(assessment.blocked_reason)
        cwd = ctx.workspace.resolve(args.cwd)
        if not cwd.is_dir():
            raise ToolError(f"Working directory {args.cwd!r} is not a directory.")
        exe = resolve_executable(argv[0], cwd, ctx)
        if IS_WINDOWS and exe.suffix.lower() in (".bat", ".cmd"):
            unsafe = [a for a in argv[1:] if _BATCH_UNSAFE.search(a)]
            if unsafe:
                raise CommandBlockedError(
                    f"{exe.name} is a batch file, and cmd.exe would interpret special "
                    f'characters in {unsafe[0]!r}. Remove characters like & | < > ^ % ! " ( ).'
                )
        default_timeout = ctx.tool_settings.command_timeout
        timeout = min(args.timeout or default_timeout, MAX_TIMEOUT)
        rel_cwd = ctx.workspace.relative(cwd)
        display = format_argv(argv)
        details = f"program: {exe}\ndirectory: {rel_cwd}\ntimeout: {timeout:.0f}s"

        def run() -> ToolResult:
            env = scrubbed_env(extra_names=ctx.secret_env_names)
            try:
                result = run_process(
                    [str(exe), *argv[1:]],
                    cwd=cwd,
                    timeout=timeout,
                    max_output_bytes=ctx.tool_settings.max_output_chars * 4,
                    env=env,
                )
            except OSError as exc:
                raise ToolError(f"Could not start {argv[0]!r}: {exc.strerror or exc}") from exc
            limit = ctx.tool_settings.max_output_chars
            parts = []
            if result.timed_out:
                parts.append(f"TIMED OUT after {timeout:.0f}s; the process tree was killed.")
            else:
                parts.append(f"exit code: {result.returncode}  ({result.duration:.1f}s)")
            for label, text, cut in (
                ("stdout", result.stdout, result.stdout_truncated),
                ("stderr", result.stderr, result.stderr_truncated),
            ):
                if text.strip():
                    note = " (truncated)" if cut else ""
                    body = truncate(ctx.redactor.redact(text.rstrip()), limit // 2, what=label)
                    parts.append(f"--- {label}{note} ---\n{body}")
            ok = not result.timed_out and result.returncode == 0
            return ToolResult("\n".join(parts), ok=ok)

        return PreparedAction(
            summary=f"run {display}" + (f" (in {rel_cwd})" if rel_cwd != "." else ""),
            details=details,
            warnings=assessment.warnings,
            argv=argv,
            auto_approvable=assessment.auto_approvable,
            run=run,
        )
