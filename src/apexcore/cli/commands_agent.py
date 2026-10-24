"""``apex`` / ``apex chat``, ``apex ask``, ``apex task``, and ``apex tools``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from apexcore.agent.loop import TurnResult
from apexcore.cli.chat import ChatSession
from apexcore.cli.common import EXIT_INTERRUPTED, guard
from apexcore.cli.session import AgentSession, open_session, workspace_warning
from apexcore.cli.ui import (
    ConsoleApprover,
    ConsoleUI,
    print_error,
    sanitize,
    stderr_console,
    stdout_console,
)
from apexcore.config import load_settings
from apexcore.errors import ConfigError
from apexcore.permissions import Decision, DenyAllApprover, PermissionPolicy
from apexcore.tools.registry import all_tools
from apexcore.usage import format_usage

ProviderOpt = Annotated[
    str | None, typer.Option("--provider", "-p", help="Provider name (default from config)")
]
ModelOpt = Annotated[
    str | None, typer.Option("--model", "-m", help="Model id (default from config)")
]
WorkspaceOpt = Annotated[
    Path | None,
    typer.Option(
        "--workspace", "-w", help="Directory the agent may access (default: current directory)"
    ),
]
NoHistoryOpt = Annotated[bool, typer.Option("--no-history", help="Do not save this session")]
ReadOnlyOpt = Annotated[
    bool, typer.Option("--read-only", help="Allow only reading files: no edits, no commands")
]


def _warn_workspace(session: AgentSession) -> None:
    if session.workspace is not None:
        warning = workspace_warning(session.workspace.root)
        if warning:
            stderr_console().print(Text("Warning: " + warning, style="yellow"))


def _report(result: TurnResult, session: AgentSession, *, show_actions: bool) -> int:
    console = stderr_console()
    if show_actions and result.actions:
        table = Table(title="Actions", title_justify="left")
        table.add_column("#", justify="right")
        table.add_column("tool")
        table.add_column("status")
        table.add_column("summary")
        for number, action in enumerate(result.actions, 1):
            style = {"ok": "green", "error": "red"}.get(action.status, "yellow")
            table.add_row(
                str(number),
                action.tool,
                Text(action.status, style=style),
                Text(sanitize(action.summary)),
            )
        console.print(table)
    pricing = session.resolved.config.pricing
    usage_line = format_usage(session.usage, pricing, reported=session.usage_reported)
    console.print(
        Text(
            f"{usage_line} | {session.resolved.name}/{session.model} | steps {result.steps}",
            style="dim",
        )
    )
    if session.conversation_id:
        console.print(
            Text(
                f"Saved as {session.conversation_id} (apex history show {session.conversation_id})",
                style="dim",
            )
        )
    if result.status == "cancelled":
        console.print(Text("Cancelled.", style="yellow"))
        return EXIT_INTERRUPTED
    if result.status == "failed" and result.error is not None:
        print_error(result.error, console)
        return result.error.exit_code
    if result.status == "max_steps":
        console.print(Text("Stopped at the step limit (agent.max_steps).", style="yellow"))
    return 0


ResumeOpt = Annotated[
    str | None, typer.Option("--resume", "-r", help="Resume a saved chat by id or title")
]
ContinueOpt = Annotated[
    bool, typer.Option("--continue", "-c", help="Continue your most recent chat here")
]
NoToolsOpt = Annotated[bool, typer.Option("--no-tools", help="Plain chat without tools")]
AutoOpt = Annotated[
    bool,
    typer.Option(
        "--auto", help="Approve edits and ordinary commands automatically (risky ones still ask)"
    ),
]


def start_chat(
    *,
    provider: str | None = None,
    model: str | None = None,
    workspace: Path | None = None,
    no_tools: bool = False,
    read_only: bool = False,
    resume: str | None = None,
    continue_last: bool = False,
    no_history: bool = False,
    auto: bool = False,
) -> int:
    """Open a session and run the interactive chat. Returns the exit code."""
    console = stdout_console()
    session = open_session(
        kind="chat",
        provider=provider,
        model=model,
        workspace=workspace,
        tool_mode="none" if no_tools else ("read" if read_only else "full"),
        read_only=read_only,
        no_history=no_history,
        approver=ConsoleApprover(console),
        auto=auto,
    )
    try:
        if resume or continue_last:
            if session.store is None:
                raise ConfigError("Resuming needs history enabled.")
            if resume:
                conversation_id = session.store.resolve_ref(resume)
            else:
                root = str(session.workspace.root) if session.workspace else str(Path.cwd())
                latest = session.store.latest("chat", workspace=root)
                conversation_id = latest.id if latest else None
            if conversation_id is None:
                console.print(Text("No earlier chat to continue; starting a new one.", style="dim"))
            else:
                session.load_history(conversation_id)
                console.print(
                    Text(
                        f"Resumed {session.title!r} ({len(session.conversation) - 1} messages).",
                        style="dim",
                    )
                )
        _warn_workspace(session)
        return ChatSession(session, console).run()
    finally:
        session.close()


@guard
def chat(
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    workspace: WorkspaceOpt = None,
    no_tools: NoToolsOpt = False,
    read_only: ReadOnlyOpt = False,
    resume: ResumeOpt = None,
    continue_last: ContinueOpt = False,
    no_history: NoHistoryOpt = False,
    auto: AutoOpt = False,
) -> None:
    """Start an interactive chat (the same as running `apex` with no command)."""
    raise typer.Exit(
        start_chat(
            provider=provider,
            model=model,
            workspace=workspace,
            no_tools=no_tools,
            read_only=read_only,
            resume=resume,
            continue_last=continue_last,
            no_history=no_history,
            auto=auto,
        )
    )


@guard
def ask(
    question: Annotated[str, typer.Argument(help="Your question; use - to read it from stdin")],
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    tools: Annotated[
        bool, typer.Option("--tools", help="Let the model read files in the workspace")
    ] = False,
    workspace: WorkspaceOpt = None,
    no_history: NoHistoryOpt = False,
) -> None:
    """Ask one question and print the answer to stdout (status goes to stderr)."""
    if question == "-":
        question = sys.stdin.read()
    question = question.strip()
    if not question:
        raise ConfigError("The question is empty.")
    session = open_session(
        kind="ask",
        provider=provider,
        model=model,
        workspace=workspace,
        tool_mode="read" if tools else "none",
        no_history=no_history,
        interactive=False,
    )
    ui = ConsoleUI(stderr_console(), text_console=stdout_console())
    try:
        _warn_workspace(session)
        result = session.send(question, ui)
    finally:
        ui.close()
        session.close()
    raise typer.Exit(_report(result, session, show_actions=False))


@guard
def task(
    description: Annotated[str, typer.Argument(help="What the agent should do")],
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    workspace: WorkspaceOpt = None,
    read_only: ReadOnlyOpt = False,
    max_steps: Annotated[
        int | None, typer.Option("--max-steps", min=1, max=100, help="Override agent.max_steps")
    ] = None,
    no_history: NoHistoryOpt = False,
    auto: AutoOpt = False,
) -> None:
    """Run an agent task in the workspace. Edits and commands ask for approval."""
    console = stdout_console()
    settings = load_settings()
    if max_steps is not None:
        settings.agent.max_steps = max_steps
    session = open_session(
        kind="task",
        provider=provider,
        model=model,
        workspace=workspace,
        tool_mode="read" if read_only else "full",
        read_only=read_only,
        no_history=no_history,
        approver=ConsoleApprover(console),
        settings=settings,
        auto=auto,
    )
    ui = ConsoleUI(console)
    try:
        _warn_workspace(session)
        if not session.tools:
            console.print(
                Text("Note: this provider is configured without tool support.", style="yellow")
            )
        elif isinstance(session.approver, DenyAllApprover) and not read_only:
            console.print(
                Text(
                    "Note: no interactive terminal, so edits and commands that "
                    "need approval will be refused"
                    + (" (--auto needs a terminal too)." if auto else "."),
                    style="yellow",
                )
            )
        result = session.send(description, ui)
    finally:
        ui.close()
        session.close()
    raise typer.Exit(_report(result, session, show_actions=True))


@guard
def tools() -> None:
    """List the agent's tools and whether each runs automatically or asks first."""
    settings = load_settings()
    policy = PermissionPolicy(settings.permissions)
    console = stdout_console()
    table = Table(title="Tools", title_justify="left")
    for column in ("tool", "risk", "default", "description"):
        table.add_column(column)
    labels = {Decision.ALLOW: "auto", Decision.ASK: "ask", Decision.DENY: "denied"}
    for tool in all_tools():
        decision = policy.static_decision(tool.risk)
        table.add_row(
            Text(tool.name, style="cyan"), tool.risk.value, labels[decision], tool.description
        )
    console.print(table)
    notes = [
        "read tools: only inside the workspace; .git and credentials files are blocked.",
        "chat and task offer all tools; ask offers read tools with --tools; --read-only "
        "removes write and execute tools.",
    ]
    prefixes = settings.permissions.auto_approve_commands
    if prefixes:
        shown = ", ".join(" ".join(p) for p in prefixes)
        notes.append(f"auto-approved command prefixes: {shown}")
    exact = settings.permissions.always_allowed_commands
    if exact:
        shown = ", ".join(" ".join(c) for c in exact)
        notes.append(f"always-allowed commands (exact): {shown}")
    for note in notes:
        console.print(Text(note, style="dim"))
