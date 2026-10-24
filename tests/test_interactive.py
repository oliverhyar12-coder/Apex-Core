"""Interactive flows: the approval prompt and the chat REPL, with scripted input."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from apexcore.cli.chat import ChatSession
from apexcore.cli.session import open_session
from apexcore.cli.ui import ConsoleApprover, ConsoleUI, sanitize
from apexcore.permissions import ApprovalRequest
from apexcore.tools.base import Risk
from conftest import write_config
from fakes import ScriptedProvider, text_response, tool_response


def console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, width=120, highlight=False, color_system=None), buffer


def scripted_input(*lines: str | type[BaseException]):
    queue = list(lines)

    def read(prompt: str) -> str:
        item = queue.pop(0)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        return item

    return read


@pytest.mark.parametrize(
    ("answer", "approved", "remember", "feedback"),
    [
        ("y", True, False, None),
        ("YES", True, False, None),
        ("a", True, True, None),
        ("", False, False, None),
        ("n", False, False, None),
        ("no, use pytest -x instead", False, False, "no, use pytest -x instead"),
    ],
)
def test_approver_answers(answer, approved, remember, feedback) -> None:
    con, buffer = console()
    approver = ConsoleApprover(con, input_fn=scripted_input(answer))
    request = ApprovalRequest("write_file", Risk.WRITE, "create a.txt", "+hello\n", ["careful"])
    response = approver.confirm(request)
    assert (response.approved, response.remember, response.feedback) == (
        approved,
        remember,
        feedback,
    )
    shown = buffer.getvalue()
    assert "create a.txt" in shown and "+hello" in shown and "WARNING: careful" in shown


def test_approver_always_not_offered_for_risky_commands() -> None:
    con, buffer = console()
    approver = ConsoleApprover(con, input_fn=scripted_input("a"))
    request = ApprovalRequest("run_command", Risk.EXECUTE, "run rm -rf x", can_remember=False)
    assert approver.confirm(request).approved is False
    assert "always" not in buffer.getvalue()


def test_approver_eof_denies() -> None:
    con, _ = console()
    approver = ConsoleApprover(con, input_fn=scripted_input(EOFError))
    assert approver.confirm(ApprovalRequest("x", Risk.WRITE, "s")).approved is False


def test_sanitize_strips_control_sequences() -> None:
    assert sanitize("a\x1b[31mred\x1b[0m\tb\nc\x07") == "a[31mred[0m\tb\nc"


def make_session(provider: ScriptedProvider, workspace: Path, *, input_fn, monkeypatch):
    write_config(
        'default_provider = "local"\n[providers.local]\nbase_url = "http://127.0.0.1:1/v1"\n'
        'model = "fake-model"\n'
    )
    monkeypatch.setattr("apexcore.cli.session.create_provider", lambda *a, **k: provider)
    con, buffer = console()
    session = open_session(
        kind="chat",
        workspace=workspace,
        approver=ConsoleApprover(con, input_fn=input_fn),
        interactive=True,
    )
    return session, con, buffer


def test_chat_approved_edit_and_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider(
        [
            tool_response(("write_file", {"path": "hello.txt", "content": "hi\n"})),
            text_response("Created hello.txt."),
        ]
    )
    inputs = scripted_input("please create hello.txt", "y", "/tools", "/clear", "/help", "/exit")
    session, con, buffer = make_session(
        provider, tmp_path, input_fn=inputs, monkeypatch=monkeypatch
    )
    code = ChatSession(session, con, input_fn=inputs).run()
    assert code == 0
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi\n"
    out = buffer.getvalue()
    assert "Approval required" in out and "Created hello.txt." in out
    assert "write_file" in out and "Started a new conversation." in out and "/model" in out
    assert len(session.conversation) == 1  # /clear kept only the system prompt


def test_chat_interrupt_during_turn_keeps_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider([KeyboardInterrupt(), text_response("Still here.")])
    inputs = scripted_input("first", "second", KeyboardInterrupt)
    session, con, buffer = make_session(
        provider, tmp_path, input_fn=inputs, monkeypatch=monkeypatch
    )
    assert ChatSession(session, con, input_fn=inputs).run() == 0
    out = buffer.getvalue()
    assert "Cancelled." in out and "Still here." in out


def test_chat_eof_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider([])
    inputs = scripted_input(EOFError)
    session, con, _ = make_session(provider, tmp_path, input_fn=inputs, monkeypatch=monkeypatch)
    assert ChatSession(session, con, input_fn=inputs).run() == 0


def test_chat_shows_provider_errors_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apexcore.errors import RateLimitError

    provider = ScriptedProvider([RateLimitError("slow down", hint="wait"), text_response("ok")])
    inputs = scripted_input("one", "two", "/exit")
    session, con, buffer = make_session(
        provider, tmp_path, input_fn=inputs, monkeypatch=monkeypatch
    )
    ChatSession(session, con, input_fn=inputs).run()
    out = buffer.getvalue()
    assert "Error: slow down" in out and "Hint: wait" in out and "ok" in out
    # The failed message was not sent twice.
    assert [m.content for m in provider.calls[1]["messages"] if m.role == "user"] == ["two"]


def test_console_ui_streams_and_marks_tools() -> None:
    con, buffer = console()
    ui = ConsoleUI(con)
    ui.thinking()
    ui.assistant_text("Hello ")
    ui.assistant_text("world")
    from apexcore.agent.loop import ActionRecord
    from apexcore.providers.base import ToolCall

    ui.tool_started(ToolCall("1", "read_file", "{}"), "read a.py")
    ui.tool_finished(ActionRecord("read_file", "read a.py", "error", "boom"))
    ui.close()
    assert buffer.getvalue() == "Hello world\n  -> read a.py\n     error: boom\n"


def test_resume_repairs_interrupted_tool_sequences() -> None:
    from apexcore.cli.session import repair_tool_sequence
    from apexcore.providers.base import Message, ToolCall

    a, b = ToolCall("a", "read_file", "{}"), ToolCall("b", "list_files", "{}")
    stored = [
        Message.user("hi"),
        Message.assistant("", [a, b]),
        Message.tool_result(a, "contents"),  # b's result was never saved
        Message.user("still there?"),
        Message.tool_result(ToolCall("zzz", "x", "{}"), "orphan"),
        Message.assistant("", [ToolCall("c", "git_status", "{}")]),  # killed right here
    ]
    repaired = repair_tool_sequence(stored)
    roles = [(m.role, m.tool_call_id) for m in repaired]
    assert roles == [
        ("user", None),
        ("assistant", None),
        ("tool", "a"),
        ("tool", "b"),
        ("user", None),
        ("assistant", None),
        ("tool", "c"),
    ]
    assert repaired[3].is_error and "session ended" in repaired[3].content
