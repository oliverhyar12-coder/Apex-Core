"""Reading chat input.

In a real terminal, input uses prompt_toolkit (like Hermes Agent):

* Enter sends; Alt+Enter, Esc then Enter, or Ctrl+J insert a new line; a
  trailing ``\\`` also continues on the next line; pasted text keeps its lines;
* Up/Down recall earlier messages (saved privately, with secrets redacted);
* typing ``/`` shows the slash commands with descriptions (Tab completes);
* Ctrl+C clears the line, or (on an empty line) asks to exit; Ctrl+D exits;
* a status bar shows the model, tokens, and mode.

Without a terminal (pipes, tests), plain ``input()`` is used, with ``\\``
continuation and ``\"\"\"`` blocks for multi-line messages.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from apexcore import paths
from apexcore.redaction import GLOBAL_REDACTOR

INPUT_HISTORY_FILE = "input_history"


class InputReader(Protocol):
    def read(self, prompt: str) -> str:
        """Return one message. Raises EOFError (exit) or KeyboardInterrupt."""
        ...


def input_history_path() -> Path:
    return paths.data_dir() / INPUT_HISTORY_FILE


def clear_input_history() -> bool:
    try:
        input_history_path().unlink()
    except FileNotFoundError:
        return False
    return True


class PlainInput:
    """Line input for pipes and dumb terminals."""

    def __init__(self, read_line: Callable[[str], str] | None = None) -> None:
        self.read_line = read_line or input

    def read(self, prompt: str) -> str:
        line = self.read_line(prompt)
        if line.strip() == '"""':
            lines = []
            while True:
                more = self.read_line("")
                if more.strip() == '"""':
                    return "\n".join(lines)
                lines.append(more)
        while line.endswith("\\"):
            line = line[:-1] + "\n" + self.read_line("... ")
        return line


class SlashCompleter(Completer):
    """Completes ``/commands`` at the start of the input, with descriptions."""

    def __init__(self, commands: dict[str, str]) -> None:
        self.commands = commands

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text or "\n" in text:
            return
        for name, description in self.commands.items():
            if name.startswith(text.lower()):
                yield Completion(name, start_position=-len(text), display_meta=description)


class RedactingHistory(FileHistory):
    """Input history on disk, owner-only, with known secrets masked."""

    def store_string(self, string: str) -> None:
        cleaned = GLOBAL_REDACTOR.redact(string)
        if cleaned.strip():
            super().store_string(cleaned)
            paths.restrict_file(Path(self.filename))


def _key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event: Any) -> None:
        buffer = event.current_buffer
        state = buffer.complete_state
        if state is not None and state.current_completion is not None:
            # Enter on a highlighted command: take it and send it in one go.
            buffer.apply_completion(state.current_completion)
        elif buffer.document.is_cursor_at_the_end and buffer.text.endswith("\\"):
            buffer.delete_before_cursor(1)
            buffer.insert_text("\n")
            return
        buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    @bindings.add("c-j")
    def _newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-c")
    def _interrupt(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.text:
            buffer.reset()
        else:
            event.app.exit(exception=KeyboardInterrupt())

    return bindings


class PromptToolkitInput:
    def __init__(
        self,
        commands: dict[str, str],
        toolbar: Callable[[], str] | None = None,
        save_history: bool = True,
        *,
        pt_input: Any = None,
        pt_output: Any = None,
    ) -> None:
        history: History = InMemoryHistory()
        if save_history:
            path = input_history_path()
            with contextlib.suppress(OSError):
                paths.ensure_private_dir(path.parent)
                history = RedactingHistory(str(path))

        def bottom_toolbar() -> FormattedText:
            return FormattedText([("class:bottom-toolbar", toolbar() if toolbar else "")])

        self.session: PromptSession[str] = PromptSession(
            history=history,
            completer=SlashCompleter(commands),
            complete_while_typing=True,
            key_bindings=_key_bindings(),
            multiline=True,
            prompt_continuation="... ",
            bottom_toolbar=bottom_toolbar if toolbar else None,
            style=Style.from_dict(
                {"bottom-toolbar": "noreverse #888888", "prompt": "bold ansicyan"}
            ),
            enable_suspend=os.name == "posix",
            input=pt_input,
            output=pt_output,
        )

    def read(self, prompt: str) -> str:
        return self.session.prompt(FormattedText([("class:prompt", prompt)]))


def terminal_supports_rich_input() -> bool:
    if os.environ.get("APEX_SIMPLE_INPUT", "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def make_reader(
    commands: dict[str, str],
    *,
    toolbar: Callable[[], str] | None = None,
    save_history: bool = True,
    console_input: Callable[[str], str] | None = None,
) -> InputReader:
    """The best available reader for the current terminal."""
    if terminal_supports_rich_input():
        try:
            return PromptToolkitInput(commands, toolbar, save_history)
        except Exception:  # e.g. an unsupported terminal type: fall back to plain input
            pass
    return PlainInput(console_input)
