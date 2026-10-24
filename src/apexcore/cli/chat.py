"""The interactive chat (``apex`` / ``apex chat``), modelled on Hermes Agent.

Keys: Enter sends · Alt+Enter or Ctrl+J for a new line · Up/Down for history ·
``/`` then Tab for commands · ``!cmd`` runs a shell command yourself ·
Ctrl+C cancels the current answer (or clears the line; twice on an empty
line exits) · Ctrl+D exits.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from apexcore import __version__
from apexcore.agent.prompts import available_personalities, personality_text
from apexcore.cli.pickers import chat_models, pick
from apexcore.cli.prompt_input import InputReader, PlainInput, make_reader
from apexcore.cli.session import AgentSession
from apexcore.cli.shell import run_user_shell
from apexcore.cli.ui import (
    ConsoleUI,
    format_tokens,
    print_error,
    sanitize,
)
from apexcore.errors import ApexError
from apexcore.memory import LIMITS, TARGETS
from apexcore.permissions import Decision
from apexcore.platform_utils import call_interruptibly
from apexcore.providers.presets import get_preset
from apexcore.tools.base import Risk
from apexcore.tools.todo import render_todos
from apexcore.usage import estimate_cost, format_usage

EXIT_WINDOW = 2.0  # seconds: a second Ctrl+C within this window exits


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    help: str
    aliases: tuple[str, ...] = ()


COMMANDS: tuple[Command, ...] = (
    Command("/help", "/help", "Show commands and keys."),
    Command("/new", "/new [title]", "Start a fresh conversation.", ("/reset", "/clear")),
    Command("/model", "/model [name | provider:name]", "Show or switch the model."),
    Command("/retry", "/retry", "Send your last message again."),
    Command("/undo", "/undo", "Remove the last exchange."),
    Command("/compress", "/compress [focus]", "Summarize the chat to save context."),
    Command("/usage", "/usage", "Token use, estimated cost, and context size."),
    Command("/tools", "/tools", "List the agent's tools and permissions."),
    Command("/plan", "/plan", "Show the agent's current plan."),
    Command("/memory", "/memory [clear]", "Show (or clear) what the agent remembers."),
    Command("/personality", "/personality [name|none]", "Change the agent's tone."),
    Command("/auto", "/auto", "Toggle auto-approval of edits and ordinary commands."),
    Command("/title", "/title <name>", "Name this conversation."),
    Command("/sessions", "/sessions", "List recent conversations."),
    Command("/resume", "/resume <id|title>", "Switch to an earlier conversation."),
    Command("/exit", "/exit", "Leave (also /quit, Ctrl+D).", ("/quit",)),
)

KEYS = (
    ("Enter", "send"),
    ("Alt+Enter / Ctrl+J", "new line (or end a line with \\)"),
    ("Up / Down", "previous messages"),
    ("/ then Tab", "complete a command"),
    ("!command", "run a shell command yourself (output is not sent to the model)"),
    ("Ctrl+C", "cancel the answer; clear the line; twice to exit"),
    ("Ctrl+D", "exit"),
)


def command_table() -> dict[str, str]:
    table: dict[str, str] = {}
    for command in COMMANDS:
        table[command.name] = command.help
        for alias in command.aliases:
            table[alias] = command.help
    return table


class QuietUI(ConsoleUI):
    """Shows the spinner but not the streamed text (used for /compress)."""

    def assistant_text(self, text: str) -> None:
        self._stop_status()


def _short_path(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


class ChatSession:
    def __init__(
        self,
        session: AgentSession,
        console: Console,
        input_fn: Callable[[str], str] | None = None,
        *,
        reader: InputReader | None = None,
    ) -> None:
        self.session = session
        self.console = console
        settings = session.settings
        self.ui = ConsoleUI(console, markdown=settings.ui.markdown, emoji=settings.ui.emoji)
        if reader is None and input_fn is not None:
            reader = PlainInput(input_fn)
        if reader is None:
            reader = make_reader(
                command_table(),
                toolbar=self.status_line,
                save_history=settings.history.save_input,
                console_input=lambda prompt: console.input(prompt),
            )
        self.reader = reader
        self._handlers: dict[str, Callable[[str], str | None]] = {
            "/help": self.cmd_help,
            "/new": self.cmd_new,
            "/reset": self.cmd_new,
            "/clear": self.cmd_new,
            "/model": self.cmd_model,
            "/retry": self.cmd_retry,
            "/undo": self.cmd_undo,
            "/compress": self.cmd_compress,
            "/usage": self.cmd_usage,
            "/tools": self.cmd_tools,
            "/plan": self.cmd_plan,
            "/memory": self.cmd_memory,
            "/personality": self.cmd_personality,
            "/auto": self.cmd_auto,
            "/title": self.cmd_title,
            "/sessions": self.cmd_sessions,
            "/resume": self.cmd_resume,
            "/exit": lambda arg: "exit",
            "/quit": lambda arg: "exit",
        }

    # --- Display -------------------------------------------------------------

    def ask(self, prompt: str) -> str:
        return self.reader.read(prompt)

    def mode_badges(self) -> list[str]:
        s = self.session
        badges = []
        if not s.tools:
            badges.append("NO TOOLS")
        elif s.policy.read_only:
            badges.append("READ-ONLY")
        if s.policy.auto:
            badges.append("AUTO")
        return badges

    def status_line(self) -> str:
        s = self.session
        parts = [f"{s.model} ({s.resolved.name})"]
        if s.usage_reported:
            parts.append(f"{format_tokens(s.usage.total_tokens)} tokens")
            cost = estimate_cost(s.usage, s.resolved.config.pricing)
            if cost is not None:
                parts.append(f"~${cost:.4f}")
        parts.append(f"~{format_tokens(s.context_tokens())} context")
        if s.title:
            parts.append(s.title[:30])
        parts.extend(self.mode_badges())
        return " " + " · ".join(parts) + " "

    def banner(self) -> None:
        s = self.session
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", justify="right")
        grid.add_column()
        grid.add_row("model", Text(f"{s.model}  ·  {s.resolved.name}"))
        if s.workspace:
            grid.add_row("workspace", Text(_short_path(s.workspace.root)))
        if s.tools:
            labels = {Decision.ALLOW: "auto", Decision.ASK: "ask", Decision.DENY: "off"}
            names = {tool.name for tool in s.tools}
            parts = ["read files"]
            if "edit_file" in names:
                parts.append(f"edit ({labels[s.policy.static_decision(Risk.WRITE)]})")
            if "run_command" in names:
                parts.append(f"run ({labels[s.policy.static_decision(Risk.EXECUTE)]})")
            if "todo" in names:
                parts.append("plan")
            if "memory" in names:
                parts.append("memory")
            grid.add_row("tools", Text(" · ".join(parts)))
        else:
            grid.add_row("tools", Text("off (plain chat)"))
        if s.memory is not None:
            counts = {target: len(s.memory.entries(target)) for target in TARGETS}
            grid.add_row("memory", Text(f"{counts['memory']} notes · {counts['user']} about you"))
        context = s.project_context
        if context is not None:
            if context.blocked:
                grid.add_row(
                    "context",
                    Text(f"{context.name} BLOCKED ({', '.join(context.findings)})", style="red"),
                )
            else:
                grid.add_row(
                    "context", Text(context.name + (" (truncated)" if context.truncated else ""))
                )
        if s.persona is not None:
            note = f" (check it: {', '.join(s.persona.findings)})" if s.persona.findings else ""
            grid.add_row("persona", Text(s.persona.name + note, style="yellow" if note else ""))
        if s.personality:
            grid.add_row("personality", Text(s.personality))
        if s.store is None:
            grid.add_row("history", Text("off for this session"))
        badges = self.mode_badges()
        if badges:
            grid.add_row("mode", Text(" · ".join(badges), style="yellow"))
        self.console.print(
            Panel(grid, title=f"APEX CORE {__version__}", title_align="left", border_style="cyan")
        )
        self.console.print(
            Text(
                "Type a message and press Enter · /help for commands and keys · Ctrl+D exits",
                style="dim",
            )
        )

    # --- Main loop ---------------------------------------------------------------

    def run(self) -> int:
        self.banner()
        last_interrupt = 0.0
        while True:
            try:
                line = self.ask("\n› " if self.ui.unicode else "\n> ")
            except EOFError:
                break
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - last_interrupt < EXIT_WINDOW:
                    break
                last_interrupt = now
                self.console.print(Text("(Ctrl+C again to exit, or type /exit)", style="dim"))
                continue
            if not line.strip():
                continue
            if line.startswith("!"):
                self.shell(line[1:])
                continue
            if line.startswith("/"):
                outcome = self.command(line.strip())
                if outcome == "exit":
                    break
                continue
            self.turn(line.strip())
        self.ui.close()
        self.goodbye()
        return 0

    def goodbye(self) -> None:
        s = self.session
        if s.conversation_id:
            self.console.print(
                Text(f"Saved as {s.conversation_id}. Continue later with: apex -c", style="dim")
            )

    def turn(self, text: str) -> None:
        self.console.print()
        started = time.monotonic()
        try:
            result = self.session.send(text, self.ui)
        except KeyboardInterrupt:
            self.ui.close()
            self.console.print(Text("Cancelled.", style="yellow"))
            return
        except ApexError as exc:  # e.g. history database problems
            self.ui.close()
            print_error(exc, self.console)
            return
        self.ui.close()
        if result.status == "cancelled":
            self.console.print(
                Text("Cancelled. (Your message is kept; /undo removes it.)", style="yellow")
            )
        elif result.status == "failed" and result.error is not None:
            print_error(result.error, self.console)
            return
        cost = estimate_cost(result.usage, self.session.resolved.config.pricing)
        self.ui.turn_summary(result, time.monotonic() - started, cost)

    def shell(self, command: str) -> None:
        s = self.session
        cwd = s.workspace.root if s.workspace else Path.cwd()
        try:
            run_user_shell(
                command, cwd=cwd, timeout=s.settings.tools.command_timeout, console=self.console
            )
        except KeyboardInterrupt:
            self.console.print(Text("Cancelled.", style="yellow"))
        except OSError as exc:
            self.console.print(Text(f"Could not run that: {exc}", style="red"))

    def command(self, text: str) -> str | None:
        name, _, arg = text.partition(" ")
        handler = self._handlers.get(name.lower())
        if handler is None:
            self.console.print(
                Text(
                    f"Unknown command {sanitize(name)}. Type /help. "
                    "(To send a message that starts with /, put a space before it.)",
                    style="yellow",
                )
            )
            return None
        try:
            return handler(arg.strip())
        except ApexError as exc:
            print_error(exc, self.console)
        except KeyboardInterrupt:
            self.console.print(Text("Cancelled.", style="yellow"))
        return None

    # --- Commands ------------------------------------------------------------------

    def cmd_help(self, arg: str) -> None:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="cyan")
        table.add_column()
        for command in COMMANDS:
            aliases = f"  (also {', '.join(command.aliases)})" if command.aliases else ""
            table.add_row(command.usage, command.help + aliases)
        self.console.print(table)
        keys = Table(show_header=False, box=None, pad_edge=False)
        keys.add_column(style="cyan")
        keys.add_column()
        for key, meaning in KEYS:
            keys.add_row(key, meaning)
        self.console.print()
        self.console.print(keys)
        self.console.print(
            Text(
                "\nApprovals: o = once, s = this session, a = always (exact command), "
                "d or Enter = deny, or type a reason (it is passed to the agent).",
                style="dim",
            )
        )

    def cmd_new(self, arg: str) -> None:
        self.session.reset(title=arg or None)
        self.console.print(Text("Started a new conversation.", style="green"))

    def cmd_model(self, arg: str) -> None:
        s = self.session
        if arg:
            prefix, sep, rest = arg.partition(":")
            if sep and rest and (prefix in s.settings.providers or get_preset(prefix)):
                s.switch_provider(prefix, rest)
            else:
                s.set_model(arg)
            self.console.print(
                Text(f"Now using {s.model} ({s.resolved.name}) for this chat.", style="green")
            )
            return
        self.console.print(Text(f"Current model: {s.model} ({s.resolved.name})"))
        self.console.print(Text("Fetching available models...", style="dim"))
        try:
            models = call_interruptibly(s.provider.list_models, s.provider.cancel)
        except ApexError as exc:
            print_error(exc, self.console)
            self.console.print(Text("Type /model <id> to switch anyway.", style="dim"))
            return
        options = [(m.id, m.description or "") for m in chat_models(models, s.model)]
        if not options:
            self.console.print(Text("The provider returned no models.", style="yellow"))
            return
        choice = pick(
            self.console,
            self.ask,
            options,
            title=f"Models from {s.resolved.name}",
            default=s.model,
            custom_label="type a model id",
        )
        if choice and choice != s.model:
            s.set_model(choice)
            self.console.print(Text(f"Now using {choice}.", style="green"))

    def cmd_retry(self, arg: str) -> None:
        text = self.session.undo()
        if text is None:
            self.console.print(Text("Nothing to retry yet.", style="yellow"))
            return
        self.console.print(Text(f"Retrying: {sanitize(text)[:80]}", style="dim"))
        self.turn(text)

    def cmd_undo(self, arg: str) -> None:
        text = self.session.undo()
        if text is None:
            self.console.print(Text("Nothing to undo.", style="yellow"))
        else:
            self.console.print(
                Text(f"Removed the last exchange ({sanitize(text)[:60]!r}).", style="green")
            )

    def cmd_compress(self, arg: str) -> None:
        before = self.session.context_tokens()
        quiet = QuietUI(self.console)
        try:
            count = self.session.compress(quiet, focus=arg)
        finally:
            quiet.close()
        if not count:
            self.console.print(Text("Not enough conversation to compress yet.", style="yellow"))
            return
        after = self.session.context_tokens()
        self.console.print(
            Text(
                f"Compressed {count} messages: ~{format_tokens(before)} → ~{format_tokens(after)} "
                f"tokens of context. Continuing as {self.session.title!r}.",
                style="green",
            )
        )

    def cmd_usage(self, arg: str) -> None:
        s = self.session
        self.console.print(
            format_usage(s.usage, s.resolved.config.pricing, reported=s.usage_reported)
        )
        self.console.print(
            Text(
                f"Context now: ~{format_tokens(s.context_tokens())} tokens "
                f"({len(s.conversation) - 1} messages). /compress shrinks it.",
                style="dim",
            )
        )

    def cmd_tools(self, arg: str) -> None:
        s = self.session
        if not s.tools:
            self.console.print("Tools are off in this chat.")
            return
        labels = {Decision.ALLOW: "auto", Decision.ASK: "ask", Decision.DENY: "off"}
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="cyan")
        table.add_column(style="dim")
        table.add_column()
        for tool in s.tools:
            decision = s.policy.static_decision(tool.risk)
            label = "auto" if s.policy.auto and decision is Decision.ASK else labels[decision]
            table.add_row(tool.name, label, tool.description.split(". ")[0])
        self.console.print(table)

    def cmd_plan(self, arg: str) -> None:
        todos = self.session.ctx.todos if self.session.ctx else []
        self.console.print(Text(render_todos(todos)))

    def cmd_memory(self, arg: str) -> None:
        memory = self.session.memory
        if memory is None:
            self.console.print("Memory is disabled (memory.enabled = false).")
            return
        if arg.lower() == "clear":
            answer = self.ask("Delete everything the agent remembers? Type yes to confirm: ")
            if answer.strip().lower() == "yes":
                memory.clear()
                self.console.print(Text("Memory cleared.", style="green"))
            else:
                self.console.print("Kept.")
            return
        for target in TARGETS:
            entries = memory.entries(target)
            used = memory.used(target)
            title = "Agent notes (MEMORY.md)" if target == "memory" else "About you (USER.md)"
            self.console.print(Text(f"{title}  {used}/{LIMITS[target]} chars", style="bold"))
            if not entries:
                self.console.print(Text("  (empty)", style="dim"))
            for entry in entries:
                self.console.print(Text("  • " + sanitize(entry).replace("\n", "\n    ")))
        self.console.print(
            Text(
                f"Files: {memory.directory} · edit with `apex memory edit` · "
                "changes apply to new conversations",
                style="dim",
            )
        )

    def cmd_personality(self, arg: str) -> None:
        s = self.session
        custom = s.settings.agent.personalities
        if not arg:
            current = s.personality or "default"
            names = ", ".join(available_personalities(custom))
            self.console.print(Text(f"Personality: {current}. Available: {names}, none."))
            return
        s.set_personality(arg)
        if s.personality:
            text = personality_text(s.personality, custom) or ""
            self.console.print(Text(f"Personality set to {s.personality}: {text}", style="green"))
        else:
            self.console.print(Text("Personality cleared.", style="green"))

    def cmd_auto(self, arg: str) -> None:
        policy = self.session.policy
        if policy.read_only:
            self.console.print(Text("This chat is read-only; /auto has no effect.", style="yellow"))
            return
        if not policy.interactive:
            self.console.print(
                Text("No interactive terminal: /auto has no effect here.", style="yellow")
            )
            return
        policy.auto = not policy.auto
        if policy.auto:
            self.console.print(
                Text(
                    "Auto mode ON: edits and ordinary commands run without asking for the rest "
                    "of this chat. Destructive or opaque commands still ask; blocked ones are "
                    "still refused. /auto again to turn it off.",
                    style="yellow",
                )
            )
        else:
            self.console.print(Text("Auto mode off: edits and commands ask first.", style="green"))

    def cmd_title(self, arg: str) -> None:
        if not arg:
            self.console.print(Text(f"Title: {self.session.title or '(none yet)'}"))
            return
        title = self.session.rename(arg)
        self.console.print(Text(f"Title set to {title!r}.", style="green"))

    def cmd_sessions(self, arg: str) -> None:
        store = self.session.store
        if store is None:
            self.console.print("History is off.")
            return
        rows = store.list_conversations(limit=15, kind="chat")
        if not rows:
            self.console.print("No saved chats yet.")
            return
        table = Table(show_header=True, box=None, pad_edge=False)
        for column in ("id", "updated", "msgs", "title"):
            table.add_column(column, style="cyan" if column == "id" else "")
        for row in rows:
            marker = " *" if row.id == self.session.conversation_id else ""
            table.add_row(
                row.id + marker,
                row.updated_at.replace("T", " ")[:16],
                str(row.message_count),
                Text(sanitize(row.title)),
            )
        self.console.print(table)
        self.console.print(Text("Switch with /resume <id or title>.", style="dim"))

    def cmd_resume(self, arg: str) -> None:
        s = self.session
        if s.store is None:
            self.console.print("History is off.")
            return
        if not arg:
            self.cmd_sessions("")
            return
        conversation_id = s.store.resolve_ref(arg)
        s.reset()
        s.load_history(conversation_id)
        self.console.print(
            Text(
                f"Resumed {s.title!r} ({len(s.conversation) - 1} messages).",
                style="green",
            )
        )
