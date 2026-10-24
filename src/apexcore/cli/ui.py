"""Terminal output: consoles, the agent UI, and the approval prompt.

Text that comes from a model, a file, or the user is always printed with Rich
markup disabled, so content such as ``[bold]`` or escape sequences cannot
restyle the terminal or fake APEX CORE's own prompts.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from apexcore.agent.loop import ActionRecord
from apexcore.errors import ApexError
from apexcore.permissions import ApprovalRequest, ApprovalResponse
from apexcore.providers.base import Message, ToolCall
from apexcore.redaction import GLOBAL_REDACTOR

# C0/C1 control characters except tab and newline: stripped from untrusted text
# so it cannot move the cursor, clear the screen, or set the window title.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
MAX_DETAIL_LINES = 200


def sanitize(text: str) -> str:
    return _CONTROL.sub("", text.replace("\r\n", "\n"))


def stdout_console() -> Console:
    return Console(highlight=False, soft_wrap=False)


def stderr_console() -> Console:
    return Console(stderr=True, highlight=False)


def print_error(exc: ApexError, console: Console | None = None) -> None:
    console = console or stderr_console()
    message = GLOBAL_REDACTOR.redact_aggressive(exc.message)
    console.print(Text("Error: ", style="bold red") + Text(sanitize(message)))
    if exc.hint:
        console.print(Text("Hint: ", style="yellow") + Text(sanitize(exc.hint)))


class ConsoleUI:
    """Shows streamed answers, tool activity, and notices.

    ``text_console`` receives the model's answer; ``console`` receives
    everything else. ``apex ask`` points them at stdout and stderr so the
    answer can be piped cleanly.
    """

    def __init__(self, console: Console, text_console: Console | None = None) -> None:
        self.console = console
        self.text_console = text_console or console
        self._status = None
        self._mid_line = False

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _end_line(self) -> None:
        if self._mid_line:
            self.text_console.out("")
            self._mid_line = False

    def thinking(self) -> None:
        self._stop_status()
        if self.console.is_terminal:
            self._status = self.console.status("Thinking...", spinner="dots")
            self._status.start()

    def assistant_text(self, text: str) -> None:
        self._stop_status()
        clean = sanitize(text)
        if clean:
            self.text_console.out(clean, end="", highlight=False)
            self._mid_line = not clean.endswith("\n")

    def assistant_done(self, message: Message) -> None:
        self._stop_status()
        self._end_line()

    def tool_started(self, call: ToolCall, summary: str) -> None:
        self._stop_status()
        self._end_line()
        self.console.print(Text("  -> ", style="cyan") + Text(sanitize(summary)))

    def tool_finished(self, record: ActionRecord) -> None:
        styles = {"ok": "green", "error": "red", "denied": "yellow", "cancelled": "yellow"}
        line = Text("     ") + Text(record.status, style=styles.get(record.status, ""))
        if record.detail and record.status != "ok":
            line += Text(": " + sanitize(record.detail)[:300])
        self.console.print(line)

    def notice(self, text: str) -> None:
        self._stop_status()
        self._end_line()
        self.console.print(Text(sanitize(text), style="yellow"))

    def close(self) -> None:
        self._stop_status()
        self._end_line()


def _render_details(details: str) -> Text:
    lines = sanitize(details).splitlines()
    shown = lines[:MAX_DETAIL_LINES]
    text = Text()
    for line in shown:
        style = ""
        if line.startswith("+") and not line.startswith("+++"):
            style = "green"
        elif line.startswith("-") and not line.startswith("---"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"
        text.append(line + "\n", style=style)
    if len(lines) > MAX_DETAIL_LINES:
        text.append(f"... ({len(lines) - MAX_DETAIL_LINES} more lines)\n", style="dim")
    return text


class ConsoleApprover:
    """Asks the user to approve an action: y = yes, n = no, a = always (this
    session). Any other text denies the action and is passed to the model as
    feedback."""

    def __init__(self, console: Console, input_fn: Callable[[str], str] | None = None) -> None:
        self.console = console
        self.input_fn = input_fn or (lambda prompt: console.input(prompt))

    def confirm(self, request: ApprovalRequest) -> ApprovalResponse:
        body = Text()
        body.append(f"{request.tool}", style="bold")
        body.append(f" ({request.risk.value})\n", style="dim")
        body.append(sanitize(request.summary) + "\n")
        for warning in request.warnings:
            body.append("WARNING: " + sanitize(warning) + "\n", style="bold red")
        if request.details:
            body.append("\n")
            body.append_text(_render_details(request.details))
        self.console.print(Panel(body, title="Approval required", border_style="yellow"))
        options = "[y]es / [N]o" + (" / [a]lways this session" if request.can_remember else "")
        self.console.print(Text(f"{options}; or type a reason to deny with feedback.", style="dim"))
        try:
            answer = self.input_fn("Allow? ").strip()
        except EOFError:
            return ApprovalResponse(approved=False)
        lowered = answer.lower()
        if lowered in ("y", "yes"):
            return ApprovalResponse(approved=True)
        if lowered in ("a", "always") and request.can_remember:
            return ApprovalResponse(approved=True, remember=True)
        if lowered in ("", "n", "no"):
            return ApprovalResponse(approved=False)
        return ApprovalResponse(approved=False, feedback=answer[:500])
