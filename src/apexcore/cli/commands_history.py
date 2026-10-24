"""``apex history``: list, show, and delete local conversations."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from apexcore import paths
from apexcore.cli.common import guard
from apexcore.cli.ui import sanitize, stdout_console
from apexcore.history import HistoryStore

app = typer.Typer(
    help="View and delete local conversation/task history (`apex history` alone lists it).",
    invoke_without_command=True,
)

_ROLE_STYLE = {"user": "bold cyan", "assistant": "bold green", "tool": "dim"}


@app.callback()
def history_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        list_()


@app.command("list")
@guard
def list_(
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 20,
    kind: Annotated[str | None, typer.Option(help="Filter: chat, ask, or task")] = None,
) -> None:
    """List recent conversations, newest first."""
    store = HistoryStore()
    rows = store.list_conversations(limit=limit, kind=kind)
    console = stdout_console()
    if not rows:
        console.print("No history yet.")
        return
    table = Table(title="History", title_justify="left")
    for column in ("id", "kind", "updated (UTC)", "status", "model", "msgs", "title"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row.id,
            row.kind,
            row.updated_at.replace("T", " ")[:19],
            row.status,
            Text(row.model or "-"),
            str(row.message_count),
            Text(sanitize(row.title)),
        )
    console.print(table)
    console.print(
        Text("Show one with `apex history show ID` (a unique prefix is enough).", style="dim")
    )


@app.command("show")
@guard
def show(
    conversation_id: Annotated[str, typer.Argument(help="Conversation id (or unique prefix)")],
    full: Annotated[bool, typer.Option("--full", help="Show complete tool output")] = False,
) -> None:
    """Show the messages and actions of one conversation."""
    store = HistoryStore()
    cid = store.resolve_id(conversation_id)
    meta = store.get_conversation(cid)
    console = stdout_console()
    console.print(Text(f"{meta.kind} {meta.id}: {sanitize(meta.title)}", style="bold"))
    console.print(
        Text(
            f"{meta.provider or '-'} / {meta.model or '-'} | {meta.status} | "
            f"created {meta.created_at} | tokens {meta.input_tokens:,} in / "
            f"{meta.output_tokens:,} out"
            + (f" | workspace {meta.workspace}" if meta.workspace else ""),
            style="dim",
        )
    )
    for message in store.get_messages(cid):
        console.print()
        label = message.role if message.role != "tool" else f"tool result ({message.name})"
        console.print(Text(label, style=_ROLE_STYLE.get(message.role, "")))
        content = sanitize(message.content)
        if message.role == "tool" and not full:
            lines = content.splitlines()
            if len(lines) > 8:
                content = "\n".join(lines[:8]) + f"\n... ({len(lines) - 8} more lines; --full)"
        if content:
            console.print(Text(content))
        for call in message.tool_calls:
            console.print(Text(f"-> {call.name} {sanitize(call.arguments)[:200]}", style="cyan"))
    actions = store.get_actions(cid)
    if actions:
        console.print()
        table = Table(title="Actions", title_justify="left")
        table.add_column("tool")
        table.add_column("status")
        table.add_column("summary")
        for action in actions:
            table.add_row(action.tool, action.status, Text(sanitize(action.summary)))
        console.print(table)


@app.command("delete")
@guard
def delete(
    conversation_id: Annotated[str, typer.Argument(help="Conversation id (or unique prefix)")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
) -> None:
    """Permanently delete one conversation."""
    store = HistoryStore()
    cid = store.resolve_id(conversation_id)
    meta = store.get_conversation(cid)
    if not yes and not typer.confirm(f"Delete {meta.kind} {cid} ({meta.title[:60]!r})?"):
        raise typer.Exit(1)
    store.delete_conversation(cid)
    stdout_console().print(Text(f"Deleted {cid}.", style="green"))


@app.command("clear")
@guard
def clear(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
) -> None:
    """Permanently delete ALL local history."""
    if not yes and not typer.confirm(f"Delete all history in {paths.history_db()}?"):
        raise typer.Exit(1)
    count = HistoryStore().clear()
    stdout_console().print(Text(f"Deleted {count} conversations.", style="green"))
