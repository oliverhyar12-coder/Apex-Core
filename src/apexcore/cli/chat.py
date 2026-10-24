"""Interactive chat loop (``apex chat``).

Keys: Enter sends a line; Ctrl+C while the model is working cancels the
current turn; Ctrl+C or Ctrl+D (Ctrl+Z then Enter on Windows) at the prompt
leaves the chat.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.table import Table
from rich.text import Text

from apexcore.cli.session import AgentSession
from apexcore.cli.ui import ConsoleUI, print_error, sanitize
from apexcore.errors import ApexError
from apexcore.permissions import Decision
from apexcore.usage import format_usage

HELP = [
    ("/help", "Show this help."),
    ("/model [name]", "Show the current model, or switch to another one."),
    ("/clear", "Start a new conversation (forget the context so far)."),
    ("/tools", "List the tools available in this session."),
    ("/usage", "Show token usage for this session."),
    ("/exit", "Leave the chat (also /quit, Ctrl+D, or Ctrl+C at the prompt)."),
]


class ChatSession:
    def __init__(
        self,
        session: AgentSession,
        console: Console,
        input_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.session = session
        self.console = console
        self.input_fn = input_fn or (lambda prompt: console.input(prompt))
        self.ui = ConsoleUI(console)

    def banner(self) -> None:
        s = self.session
        tools = "off"
        if s.tools:
            parts = ["read: auto"]
            for label, risk in (("write", "write"), ("commands", "execute")):
                if any(t.risk.value == risk for t in s.tools):
                    parts.append(f"{label}: ask")
            tools = ", ".join(parts)
        self.console.print(Text("APEX CORE chat", style="bold"))
        self.console.print(
            Text(f"provider {s.resolved.name} | model {s.model} | tools {tools}", style="dim")
        )
        if s.workspace:
            self.console.print(Text(f"workspace {s.workspace.root}", style="dim"))
        if s.store is None:
            self.console.print(Text("history: off for this session", style="dim"))
        self.console.print(Text("Type /help for commands, /exit to leave.", style="dim"))

    def run(self) -> int:
        self.banner()
        while True:
            try:
                line = self.input_fn("\nyou> ")
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                break
            text = line.strip()
            if not text:
                continue
            if text.startswith("/"):
                if self.command(text) == "exit":
                    break
                continue
            self.turn(text)
        self.ui.close()
        return 0

    def turn(self, text: str) -> None:
        self.console.print()
        try:
            result = self.session.send(text, self.ui)
        except ApexError as exc:  # e.g. history database problems
            self.ui.close()
            print_error(exc, self.console)
            return
        self.ui.close()
        if result.status == "cancelled":
            self.console.print(Text("Cancelled. (The conversation is kept.)", style="yellow"))
        elif result.status == "failed" and result.error is not None:
            print_error(result.error, self.console)

    def command(self, text: str) -> str | None:
        name, _, arg = text.partition(" ")
        name = name.lower()
        arg = arg.strip()
        if name in ("/exit", "/quit"):
            return "exit"
        if name == "/help":
            table = Table(show_header=False, box=None, pad_edge=False)
            for cmd, desc in HELP:
                table.add_row(Text(cmd, style="cyan"), desc)
            self.console.print(table)
        elif name == "/model":
            if arg:
                self.session.model = arg
                self.console.print(Text(f"Model set to {arg} for this session.", style="green"))
            else:
                self.console.print(
                    f"Current model: {self.session.model} (provider {self.session.resolved.name})."
                    " Use /model NAME to switch; `apex models` lists models."
                )
        elif name == "/clear":
            self.session.reset()
            self.console.print(Text("Started a new conversation.", style="green"))
        elif name == "/tools":
            if not self.session.tools:
                self.console.print("Tools are off in this session.")
            for tool in self.session.tools:
                decision = self.session.policy.static_decision(tool.risk)
                label = {Decision.ALLOW: "auto", Decision.ASK: "ask", Decision.DENY: "denied"}
                self.console.print(
                    Text(f"  {tool.name:<13}", style="cyan")
                    + Text(f"{tool.risk.value:<8} {label[decision]:<7}", style="dim")
                    + Text(tool.description.split(". ")[0])
                )
        elif name == "/usage":
            pricing = self.session.resolved.config.pricing
            self.console.print(
                format_usage(self.session.usage, pricing, reported=self.session.usage_reported)
            )
        else:
            self.console.print(
                Text(f"Unknown command {sanitize(name)}. Type /help.", style="yellow")
            )
        return None
