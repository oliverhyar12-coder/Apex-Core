"""Terminal output: consoles, the live agent UI, and the approval prompt.

Text that comes from a model, a file, or the user is always printed with Rich
markup disabled (or through the Markdown renderer, which does not interpret
Rich markup), and control characters are stripped first, so content cannot
restyle the terminal, move the cursor, or fake APEX CORE's own prompts.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, ClassVar

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.live import Live
from rich.markdown import CodeBlock, Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from apexcore.agent.loop import ActionRecord, TurnResult
from apexcore.cli.branding import MARKDOWN_THEME
from apexcore.errors import ApexError
from apexcore.permissions import ApprovalRequest, ApprovalResponse
from apexcore.providers.base import Message, ToolCall
from apexcore.redaction import GLOBAL_REDACTOR

# C0/C1 control characters except tab and newline: stripped from untrusted text
# so it cannot move the cursor, clear the screen, or set the window title.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
MAX_DETAIL_LINES = 200
FEED = "┊"

# Tool icons for the activity feed: (emoji, plain fallback).
ICONS: dict[str, tuple[str, str]] = {
    "list_files": ("📁", "ls"),
    "read_file": ("📖", "rd"),
    "search_files": ("🔍", "sr"),
    "git_status": ("🌿", "gt"),
    "git_diff": ("🌿", "gt"),
    "todo": ("📋", "pl"),
    "memory": ("💾", "mm"),
    "edit_file": ("📝", "ed"),
    "write_file": ("📄", "wr"),
    "run_command": ("💻", "$ "),
}
STATUS_MARKS = {"ok": ("✓", "ok"), "error": ("✗", "x"), "denied": ("⊘", "no"),
                "cancelled": ("⊘", "--")}  # fmt: skip


def sanitize(text: str) -> str:
    return _CONTROL.sub("", text.replace("\r\n", "\n"))


def stdout_console() -> Console:
    return Console(highlight=False, soft_wrap=False)


def stderr_console() -> Console:
    return Console(stderr=True, highlight=False)


def supports_unicode(console: Console) -> bool:
    encoding = (console.encoding or "").lower().replace("-", "")
    return encoding.startswith("utf")


def print_error(exc: ApexError, console: Console | None = None) -> None:
    console = console or stderr_console()
    message = GLOBAL_REDACTOR.redact_aggressive(exc.message)
    console.print(Text("Error: ", style="bold red") + Text(sanitize(message)))
    if exc.hint:
        console.print(Text("Hint: ", style="yellow") + Text(sanitize(exc.hint)))


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m{rest:02d}s"


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


class _Elapsed:
    """A label whose elapsed-time suffix updates on every spinner refresh."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.started = time.monotonic()

    def __rich__(self) -> Text:
        return Text(f"{self.label} ({time.monotonic() - self.started:.1f}s)", style="dim")


# --- Incremental Markdown -----------------------------------------------------


_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def split_complete_blocks(text: str) -> tuple[str, str]:
    """Split streamed Markdown into (finished blocks, unfinished tail).

    A block ends at a blank line that is not inside a fenced code block, so
    finished blocks can be rendered once and never change.
    """
    lines = text.split("\n")
    fence: str | None = None
    boundary = 0  # index of the first line of the unfinished tail
    for index, line in enumerate(lines[:-1]):  # the last line may still be growing
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0] * 3
            elif marker.startswith(fence):
                fence = None
                continue
        if fence is None and not line.strip():
            boundary = index + 1
    if boundary == 0:
        return "", text
    return "\n".join(lines[:boundary]).rstrip("\n"), "\n".join(lines[boundary:])


def _tail_for_live(tail: str, max_lines: int) -> str:
    """The last ``max_lines`` lines of the unfinished block, keeping an open
    code fence so the visible part still renders as code."""
    lines = tail.split("\n")
    if len(lines) <= max_lines:
        return tail
    opener = next((line for line in lines if _FENCE.match(line)), None)
    kept = lines[-max_lines:]
    if opener is not None and opener not in kept:
        kept = [opener, *kept[1:]]
    return "\n".join(kept)


class _TightCodeBlock(CodeBlock):
    """Code blocks without Rich's vertical padding (the background sets them apart)."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip()
        yield Syntax(code, self.lexer_name, theme=self.theme, word_wrap=True, padding=(0, 1))


class TerminalMarkdown(Markdown):
    elements: ClassVar[dict[str, type[Any]]] = {
        **Markdown.elements,
        "fence": _TightCodeBlock,
        "code_block": _TightCodeBlock,
    }


class MarkdownStream:
    """Render a streamed answer as Markdown without flicker: finished blocks are
    printed permanently, and only the block being written is redrawn live."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.buffer = ""
        self.live: Live | None = None
        self._blocks = 0

    def _print_block(self, text: str) -> None:
        # Render off-screen, trim the blank lines Rich adds around some elements
        # (lists, code), and print the result as ordinary styled text with
        # exactly one blank line between blocks.
        capture = Console(
            width=self.console.width,
            color_system=self.console.color_system,  # type: ignore[arg-type]
            force_terminal=True,
            highlight=False,
            theme=MARKDOWN_THEME,
        )
        with capture.capture() as captured:
            capture.print(self._markdown(text))
        lines = captured.get().split("\n")
        visible = [bool(_ANSI.sub("", line).strip()) for line in lines]
        if not any(visible):
            return
        first = visible.index(True)
        last = len(visible) - 1 - visible[::-1].index(True)
        if self._blocks:
            self.console.print()
        self.console.print(Text.from_ansi("\n".join(lines[first : last + 1])))
        self._blocks += 1

    def _markdown(self, text: str) -> Markdown:
        return TerminalMarkdown(text, hyperlinks=False, code_theme="monokai")

    def feed(self, delta: str) -> None:
        self.buffer += delta
        done, tail = split_complete_blocks(self.buffer)
        if done.strip():
            self._print_block(done)
        self.buffer = tail
        if not tail.strip():
            return
        visible = _tail_for_live(tail, max(4, self.console.height - 4))
        if self.live is None:
            self.live = Live(
                self._markdown(visible),
                console=self.console,
                transient=True,
                refresh_per_second=10,
                vertical_overflow="crop",
            )
            self.live.start()
        else:
            self.live.update(self._markdown(visible))

    def finish(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
        if self.buffer.strip():
            self._print_block(self.buffer)
        self.buffer = ""


# --- Agent UI -------------------------------------------------------------------


class ConsoleUI:
    """Shows the thinking spinner, streamed answers, and the tool activity feed.

    ``text_console`` receives the model's answer and ``console`` everything else;
    ``apex ask`` points them at stdout and stderr so the answer pipes cleanly.
    Markdown rendering is used only for interactive terminals.
    """

    def __init__(
        self,
        console: Console,
        text_console: Console | None = None,
        *,
        markdown: bool = False,
        emoji: bool = True,
    ) -> None:
        self.console = console
        self.text_console = text_console or console
        self.markdown = markdown and self.text_console.is_terminal
        self.unicode = supports_unicode(console)
        if self.markdown:
            self.text_console.push_theme(MARKDOWN_THEME)
        self.emoji = emoji and self.unicode
        self._status = None
        self._mid_line = False
        self._stream: MarkdownStream | None = None

    # --- helpers ---

    def _stop_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _end_text(self) -> None:
        if self._stream is not None:
            self._stream.finish()
            self._stream = None
        if self._mid_line:
            self.text_console.out("")
            self._mid_line = False

    def _spinner(self, label: str) -> None:
        self._stop_status()
        if self.console.is_terminal:
            spinner = "dots" if self.unicode else "line"
            self._status = self.console.status(_Elapsed(label), spinner=spinner)
            self._status.start()

    def icon(self, tool: str) -> str:
        emoji, plain = ICONS.get(tool, ("🔧", "--"))
        return emoji if self.emoji else plain

    # --- AgentUI protocol ---

    def thinking(self) -> None:
        self._spinner("thinking…" if self.unicode else "thinking...")

    def assistant_text(self, text: str) -> None:
        self._stop_status()
        clean = sanitize(text)
        if not clean:
            return
        if self.markdown:
            if self._stream is None:
                self._stream = MarkdownStream(self.text_console)
            self._stream.feed(clean)
        else:
            self.text_console.out(clean, end="", highlight=False)
            self._mid_line = not clean.endswith("\n")

    def assistant_done(self, message: Message) -> None:
        self._stop_status()
        self._end_text()

    def tool_started(self, call: ToolCall, summary: str) -> None:
        self._stop_status()
        self._end_text()

    def tool_running(self, call: ToolCall, summary: str) -> None:
        self._spinner(f"{FEED} {self.icon(call.name)} {sanitize(summary)}")

    def tool_finished(self, record: ActionRecord) -> None:
        self._stop_status()
        self._end_text()
        styles = {"ok": "green", "error": "red", "denied": "yellow", "cancelled": "yellow"}
        mark_unicode, mark_plain = STATUS_MARKS.get(record.status, ("?", "?"))
        mark = mark_unicode if self.unicode else mark_plain
        feed = FEED if self.unicode else "|"
        line = Text(f"{feed} ", style="dim") + Text(f"{self.icon(record.tool)} ")
        line += Text(sanitize(record.summary))
        line += Text(f"  {mark}", style=styles.get(record.status, ""))
        if record.duration is not None:
            line += Text(f" {format_duration(record.duration)}", style="dim")
        # The output tail (record.display) already explains a failed command.
        if record.detail and record.status != "ok" and not record.display:
            line += Text(": " + sanitize(record.detail)[:300], style=styles.get(record.status, ""))
        self.console.print(line)
        if record.display:
            for display_line in sanitize(record.display).splitlines()[:12]:
                self.console.print(Text(f"{feed}    {display_line}", style="dim"))

    def notice(self, text: str) -> None:
        self._stop_status()
        self._end_text()
        self.console.print(Text(sanitize(text), style="yellow"))

    # --- extras ---

    def turn_summary(self, result: TurnResult, seconds: float, cost: float | None = None) -> None:
        """A dim one-line recap after each chat turn."""
        if not self.console.is_terminal:
            return
        parts = []
        tools = len(result.actions)
        if tools:
            parts.append(f"{tools} tool{'s' if tools != 1 else ''}")
        if result.usage_reported:
            parts.append(f"{format_tokens(result.usage.total_tokens)} tokens")
        if cost is not None:
            parts.append(f"~${cost:.4f}")
        parts.append(format_duration(seconds))
        rule = "─" if self.unicode else "-"
        self.console.print(Text(f"{rule * 2} {' · '.join(parts)} {rule * 2}", style="dim"))

    def close(self) -> None:
        self._stop_status()
        self._end_text()


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
    text.rstrip()
    return text


class ConsoleApprover:
    """Asks before an edit or command, like Hermes Agent:

    ``o`` once, ``s`` for this session, ``a`` always (exact commands only; saved
    to config.toml), ``d`` deny (the default). Anything else you type denies the
    action and is passed to the model as feedback.
    """

    def __init__(self, console: Console, input_fn: Callable[[str], str] | None = None) -> None:
        self.console = console
        self.input_fn = input_fn or (lambda prompt: console.input(prompt))

    def confirm(self, request: ApprovalRequest) -> ApprovalResponse:
        title = {"write": "Edit file?", "execute": "Run command?", "memory": "Save to memory?"}
        body_parts = [Text(sanitize(request.summary), style="bold")]
        for warning in request.warnings:
            body_parts.append(Text("WARNING: " + sanitize(warning), style="bold red"))
        if request.details:
            body_parts.append(_render_details(request.details))
        self.console.print(
            Panel(
                Group(*body_parts),
                title=title.get(request.risk.value, "Approve?"),
                title_align="left",
                border_style="yellow",
            )
        )
        choices = ["[o]nce"]
        if request.can_remember:
            choices.append("[s]ession")
        if request.can_always:
            choices.append("[a]lways")
        choices.append("[d]eny")
        hint = "  (Enter = deny, or type a reason)"
        self.console.print(Text(" | ".join(choices) + hint, style="dim"))
        while True:
            try:
                answer = self.input_fn("Allow? ").strip()
            except EOFError:
                return ApprovalResponse(approved=False)
            lowered = answer.lower()
            if lowered in ("o", "once", "y", "yes"):
                return ApprovalResponse(approved=True, scope="once")
            if lowered in ("s", "session"):
                if request.can_remember:
                    return ApprovalResponse(approved=True, scope="session")
            elif lowered in ("a", "always"):
                if request.can_always:
                    return ApprovalResponse(approved=True, scope="always")
            elif lowered in ("", "d", "deny", "n", "no"):
                return ApprovalResponse(approved=False)
            else:
                return ApprovalResponse(approved=False, feedback=answer[:500])
            # An option that is not offered for this action: ask again.
            self.console.print(
                Text(f"{answer!r} is not available here. Choose: {' | '.join(choices)}", "yellow")
            )
