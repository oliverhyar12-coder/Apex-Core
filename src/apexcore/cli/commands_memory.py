"""``apex memory``: see, edit, or clear what the agent remembers across sessions."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.text import Text

from apexcore.cli.common import guard, open_in_editor
from apexcore.cli.ui import sanitize, stdout_console
from apexcore.memory import LIMITS, TARGETS, MemoryStore, Target

app = typer.Typer(
    help="See, edit, or clear the agent's memory (`apex memory` alone shows it).",
    invoke_without_command=True,
)

TargetOpt = Annotated[
    str | None,
    typer.Option("--target", "-t", help="memory (agent notes) or user (about you); default both"),
]


def _targets(target: str | None) -> list[Target]:
    if target is None:
        return list(TARGETS)
    if target not in TARGETS:
        raise typer.BadParameter("use 'memory' or 'user'")
    return [target]  # type: ignore[list-item]


@app.callback()
def memory_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        show()


@app.command("show")
@guard
def show(target: TargetOpt = None) -> None:
    """Print the saved memory."""
    store = MemoryStore()
    console = stdout_console()
    for name in _targets(target):
        title = "Agent notes (MEMORY.md)" if name == "memory" else "About you (USER.md)"
        console.print(Text(f"{title}  {store.used(name)}/{LIMITS[name]} chars", style="bold"))
        entries = store.entries(name)
        if not entries:
            console.print(Text("  (empty)", style="dim"))
        for entry in entries:
            console.print(Text("  • " + sanitize(entry).replace("\n", "\n    ")))
    console.print(Text(f"Stored in {store.directory}", style="dim"))


@app.command("edit")
@guard
def edit(target: Annotated[str, typer.Argument(help="memory or user")] = "memory") -> None:
    """Open a memory file in your editor ($EDITOR; Notepad on Windows)."""
    name = _targets(target)[0]
    store = MemoryStore()
    path = store.path(name)
    if not path.exists():
        store.directory.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    open_in_editor(path)
    stdout_console().print(
        Text(f"Saved {path.name}. Entries are separated by a line with only §.", style="green")
    )


@app.command("clear")
@guard
def clear(
    target: TargetOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
) -> None:
    """Delete saved memory."""
    names = _targets(target)
    if not yes and not typer.confirm(f"Delete {' and '.join(names)} memory?"):
        raise typer.Exit(1)
    store = MemoryStore()
    for name in names:
        store.clear(name)
    stdout_console().print(Text("Memory cleared.", style="green"))


@app.command("path")
@guard
def path() -> None:
    """Print where memory is stored."""
    typer.echo(MemoryStore().directory)
