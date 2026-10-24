"""Reading chat input.

In a real terminal, input uses prompt_toolkit (like Hermes Agent):

* Enter sends; Alt+Enter, Esc then Enter, or Ctrl+J insert a new line; a
  trailing ``\\`` also continues on the next line; pasted text keeps its lines;
* Up/Down recall earlier messages (saved privately, with secrets redacted);
* typing ``/`` shows the slash commands with descriptions (Tab completes), and
  ``@`` completes file names in the workspace;
* Ctrl+C clears the line, or (on an empty line) asks to exit; Ctrl+D exits;
* a rule above the prompt and a status bar below it show the model, context
  use, and mode, like Hermes Agent.

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
from prompt_toolkit.application import get_app
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style

from apexcore import paths
from apexcore.errors import WorkspaceError
from apexcore.redaction import GLOBAL_REDACTOR
from apexcore.tools.workspace import Workspace

INPUT_HISTORY_FILE = "input_history"
MAX_FILE_COMPLETIONS = 60
# Called with the terminal width; returns the status bar's contents.
Toolbar = Callable[[int], AnyFormattedText]


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
    """Completes ``/commands`` at the start of the input (with descriptions) and
    ``@path`` file mentions anywhere (when a workspace is given)."""

    def __init__(self, commands: dict[str, str], workspace: Workspace | None = None) -> None:
        self.commands = commands
        self.workspace = workspace

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)
        if word.startswith("@") and self.workspace is not None:
            yield from self._files(word)
            return
        if not text.startswith("/") or " " in text or "\n" in text:
            return
        for name, description in self.commands.items():
            if name.startswith(text.lower()):
                yield Completion(name, start_position=-len(text), display_meta=description)

    def _files(self, word: str) -> Iterable[Completion]:
        assert self.workspace is not None
        partial = word[1:].replace("\\", "/")
        folder, _, prefix = partial.rpartition("/")
        try:
            base = self.workspace.resolve(folder or ".")
            entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (WorkspaceError, OSError):
            return
        shown = 0
        for entry in entries:
            name = entry.name
            if not name.lower().startswith(prefix.lower()) or name.lower() == ".git":
                continue
            is_dir = entry.is_dir()
            if is_dir and name in self.workspace.ignore_dirs:
                continue
            if not is_dir and self.workspace.is_secret(Path(entry.path)):
                continue
            rel = f"{folder}/{name}" if folder else name
            yield Completion(
                "@" + rel + ("/" if is_dir else ""),
                start_position=-len(word),
                display=name + ("/" if is_dir else ""),
                display_meta="folder" if is_dir else "file",
            )
            shown += 1
            if shown >= MAX_FILE_COMPLETIONS:
                return


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


STYLE = Style.from_dict(
    {
        "bottom-toolbar": "noreverse",
        "rule": "#1e40af",
        "prompt": "bold #38bdf8",
        "placeholder": "italic #64748b",
        "status": "#94a3b8",
        "status.model": "bold #7dd3fc",
        "status.icon": "#38bdf8",
        "status.bar": "#3b82f6",
        "status.empty": "#334155",
        "status.badge": "bold #facc15",
        "status.key": "bold #38bdf8",
        "status.hint": "#60a5fa",
        "completion-menu.completion": "bg:#172554 #dbeafe",
        "completion-menu.completion.current": "bg:#2563eb #ffffff bold",
        "completion-menu.meta.completion": "bg:#0f1d40 #93c5fd",
        "completion-menu.meta.completion.current": "bg:#1d4ed8 #e0f2fe",
        "scrollbar.background": "bg:#172554",
        "scrollbar.button": "bg:#3b82f6",
    }
)


class PromptToolkitInput:
    def __init__(
        self,
        commands: dict[str, str],
        toolbar: Toolbar | None = None,
        save_history: bool = True,
        *,
        pt_input: Any = None,
        pt_output: Any = None,
        workspace: Workspace | None = None,
        placeholder: Callable[[], str] | None = None,
        unicode: bool = True,
    ) -> None:
        self.placeholder = placeholder
        self.rule_char = "─" if unicode else "-"
        history: History = InMemoryHistory()
        if save_history:
            path = input_history_path()
            with contextlib.suppress(OSError):
                paths.ensure_private_dir(path.parent)
                history = RedactingHistory(str(path))

        def bottom_toolbar() -> FormattedText:
            width = _width()
            content = toolbar(width) if toolbar else ""
            fragments = [("class:status", content)] if isinstance(content, str) else content
            return FormattedText([("class:rule", self.rule_char * width + "\n"), *fragments])

        self.session: PromptSession[str] = PromptSession(
            history=history,
            completer=SlashCompleter(commands, workspace),
            complete_while_typing=True,
            key_bindings=_key_bindings(),
            multiline=True,
            prompt_continuation="... ",
            bottom_toolbar=bottom_toolbar if toolbar else None,
            style=STYLE,
            enable_suspend=os.name == "posix",
            input=pt_input,
            output=pt_output,
        )

        self._keep_status_under_input()

    def _keep_status_under_input(self) -> None:
        """Keep the status bar directly under the input line, as in Hermes Agent.

        prompt_toolkit lets the input window grow to the bottom of the terminal,
        which pushes the bottom toolbar down there too. Grow it only while the
        completion menu needs room.
        """
        session = self.session
        for window in session.layout.find_all_windows():
            control = window.content
            if isinstance(control, BufferControl) and control.buffer is session.default_buffer:

                def height() -> Dimension:
                    menu = session.default_buffer.complete_state is not None
                    return Dimension(min=session.reserve_space_for_menu) if menu else Dimension()

                window.height = height
                window.dont_extend_height = to_filter(True)

    def read(self, prompt: str) -> str:
        """Show a rule, then ``prompt`` (leading newlines go above the rule)."""
        gap = "\n" * (len(prompt) - len(prompt.lstrip("\n")))

        def message() -> FormattedText:
            rule = self.rule_char * _width()
            return FormattedText(
                [("", gap), ("class:rule", rule + "\n"), ("class:prompt", prompt.lstrip("\n"))]
            )

        hint = self.placeholder() if self.placeholder else ""
        return self.session.prompt(
            message, placeholder=FormattedText([("class:placeholder", hint)]) if hint else None
        )


def _width() -> int:
    try:
        return max(get_app().output.get_size().columns - 1, 10)
    except Exception:  # no running application (should not happen while prompting)
        return 79


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
    toolbar: Toolbar | None = None,
    save_history: bool = True,
    console_input: Callable[[str], str] | None = None,
    workspace: Workspace | None = None,
    placeholder: Callable[[], str] | None = None,
    unicode: bool = True,
) -> InputReader:
    """The best available reader for the current terminal."""
    if terminal_supports_rich_input():
        try:
            return PromptToolkitInput(
                commands,
                toolbar,
                save_history,
                workspace=workspace,
                placeholder=placeholder,
                unicode=unicode,
            )
        except Exception:  # e.g. an unsupported terminal type: fall back to plain input
            pass
    return PlainInput(console_input)
