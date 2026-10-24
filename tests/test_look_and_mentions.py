"""The Hermes-style start screen, status bar, and @file mentions."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from rich.console import Console

from apexcore.cli.branding import (
    TIGER_WIDTH,
    big_title,
    tiger_logo,
    tiger_pixels,
    title_lines,
)
from apexcore.cli.chat import ChatSession
from apexcore.cli.mentions import attach_files
from apexcore.cli.prompt_input import PromptToolkitInput, SlashCompleter
from apexcore.cli.session import open_session
from apexcore.redaction import Redactor
from apexcore.tools.workspace import Workspace
from conftest import write_config
from fakes import ScriptedProvider, text_response

SECRET = "sk-test-attached-secret-0123456789"


def fancy_console(width: int = 140) -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=width, force_terminal=True, color_system="truecolor", highlight=False
    )
    return console, buffer


def plain_console(width: int = 120) -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, width=width, color_system=None, highlight=False), buffer


def chat(tmp_path: Path, monkeypatch, console: Console, provider=None, lines=()) -> ChatSession:
    write_config(
        'default_provider = "local"\n[providers.local]\nbase_url = "http://127.0.0.1:1/v1"\n'
        'model = "fake-model"\n'
    )
    provider = provider or ScriptedProvider([])
    monkeypatch.setattr("apexcore.cli.session.create_provider", lambda *a, **k: provider)
    session = open_session(kind="chat", workspace=tmp_path, interactive=True)
    queue = list(lines)

    def read(prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ChatSession(session, console, input_fn=read)


# --- Branding --------------------------------------------------------------------------


def test_title_font_lines_line_up() -> None:
    lines = title_lines()
    assert len(lines) == 6 and len({len(line) for line in lines}) == 1
    assert len(lines[0]) <= 78  # fits an 80-column terminal


def test_tiger_is_symmetric_and_fits_its_column() -> None:
    pixels = tiger_pixels()
    assert all(row == row[::-1] for row in pixels)
    assert {len(row) for row in pixels} == {TIGER_WIDTH}
    console, _ = fancy_console()
    logo = tiger_logo(console)
    assert logo is not None and len(logo.plain.splitlines()) == (len(pixels) + 1) // 2


def test_pictures_need_a_colour_unicode_terminal() -> None:
    console, _ = plain_console()
    assert big_title(console) is None and tiger_logo(console) is None
    narrow, _ = fancy_console(width=60)
    assert big_title(narrow) is None
    wide, _ = fancy_console()
    title = big_title(wide)
    assert title is not None and "█" in title.plain and "╗" in title.plain


def test_banner_on_a_colour_terminal(tmp_path: Path, monkeypatch) -> None:
    console, buffer = fancy_console()
    chat(tmp_path, monkeypatch, console).banner()
    out = buffer.getvalue()
    assert "█" in out and "▀" in out  # title and tiger
    for text in ("Available Tools", "Commands", "Session", "edit (asks first)", "/compress",
                 "fake-model", "Welcome to APEX CORE!", "Tip:", "/help for commands"):  # fmt: skip
        assert text in out, text


def test_banner_falls_back_to_text(tmp_path: Path, monkeypatch) -> None:
    console, buffer = plain_console()
    chat(tmp_path, monkeypatch, console).banner()
    out = buffer.getvalue()
    assert "█" not in out and "▀" not in out
    assert "APEX CORE v" in out and "Available Tools" in out and "fake-model" in out


# --- Status bar ------------------------------------------------------------------------


def test_status_bar(tmp_path: Path, monkeypatch) -> None:
    console, _ = plain_console()
    session = chat(tmp_path, monkeypatch, console)
    text = "".join(part for _, part in session.status_fragments(120))
    assert text.startswith(" ◆ fake-model · ctx ~") and "] --" in text
    assert text.endswith("/ commands · @ files · ! shell ") and get_cwidth(text) == 120
    session.session.resolved.config.context_window = 1000
    assert "%" in "".join(part for _, part in session.status_fragments(120))
    narrow = "".join(part for _, part in session.status_fragments(50))
    assert "fake-model" in narrow and "! shell" not in narrow


def test_prompt_keeps_the_status_bar_under_the_input() -> None:
    with create_pipe_input() as pipe:
        reader = PromptToolkitInput(
            {}, toolbar=lambda width: [("class:status", "S" * width)], save_history=False,
            pt_input=pipe, pt_output=DummyOutput(), placeholder=lambda: "Ask anything",
        )  # fmt: skip
        session = reader.session
        # The same window appears twice in the tree (with and without a frame).
        windows = {
            id(w): w
            for w in session.layout.find_all_windows()
            if isinstance(w.content, BufferControl) and w.content.buffer is session.default_buffer
        }
        [window] = windows.values()
        assert window.dont_extend_height()
        pipe.send_text("hello\r")
        assert reader.read("\n❯ ") == "hello"


# --- @file mentions -------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text(f"token {SECRET}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=1\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02" * 50)
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    return tmp_path


def test_at_completes_workspace_files(project: Path) -> None:
    completer = SlashCompleter({}, Workspace(project))

    def texts(typed: str) -> list[str]:
        return [c.text for c in completer.get_completions(Document(typed), CompleteEvent())]

    assert texts("look at @sr") == ["@src/"]
    assert texts("look at @src/a") == ["@src/app.py"]
    everything = texts("@")
    assert "@src/" in everything and "@notes.txt" in everything
    assert not {"@.env", "@.git/", "@node_modules/"} & set(everything)
    assert texts("@../") == []  # outside the workspace
    assert SlashCompleter({}).get_completions(Document("@sr"), CompleteEvent()) is not None


def test_attach_files(project: Path) -> None:
    redactor = Redactor([SECRET])
    message = (
        "see @src/app.py, @notes.txt and @.env, @missing.py, @src, @blob.bin, "
        "@../outside.txt, @alice, and mail me@example.com"
    )
    text, attached, problems = attach_files(message, Workspace(project), redactor)
    assert [a.path for a in attached] == ["src/app.py", "notes.txt"]
    assert text.startswith(message)
    assert '<attached_file path="src/app.py">\ndef add(a, b):' in text
    assert SECRET not in text and "[REDACTED]" in text
    joined = "\n".join(problems)
    for name in ("@.env", "@missing.py", "@src is a folder", "@blob.bin", "@../outside.txt"):
        assert name in joined, name
    assert "alice" not in joined and "example.com" not in joined
    assert len(problems) == 5


def test_chat_sends_attached_files(project: Path, monkeypatch) -> None:
    provider = ScriptedProvider([text_response("It adds two numbers.")])
    console, buffer = plain_console()
    chat(project, monkeypatch, console, provider, ["what does @src/app.py do?"]).run()
    sent = provider.calls[0]["messages"][-1].content
    assert sent.startswith("what does @src/app.py do?")
    assert '<attached_file path="src/app.py">' in sent
    assert "attached src/app.py (" in buffer.getvalue()
