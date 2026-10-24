from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from apexcore import paths
from apexcore.errors import HistoryError
from apexcore.history import SCHEMA_VERSION, HistoryStore
from apexcore.providers.base import Message, ToolCall, Usage
from apexcore.redaction import Redactor


def test_initialize_creates_schema() -> None:
    store = HistoryStore()
    assert store.initialize() == SCHEMA_VERSION
    assert paths.history_db().exists()


def test_roundtrip_with_tool_calls() -> None:
    store = HistoryStore()
    cid = store.create_conversation("task", "Fix the tests", provider="p", model="m")
    call = ToolCall(id="call_1", name="read_file", arguments='{"path": "a.py"}')
    store.add_message(cid, Message.user("hello"))
    store.add_message(cid, Message.assistant("", [call]))
    store.add_message(cid, Message.tool_result(call, "contents", is_error=True))
    store.add_action(cid, "read_file", "read a.py", "ok")
    store.update_conversation(cid, status="completed", usage=Usage(100, 20))
    messages = store.get_messages(cid)
    assert [m.role for m in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls[0].name == "read_file"
    assert messages[1].tool_calls[0].parse_arguments() == {"path": "a.py"}
    assert messages[2].tool_call_id == "call_1" and messages[2].is_error
    meta = store.get_conversation(cid)
    assert (meta.status, meta.input_tokens, meta.output_tokens) == ("completed", 100, 20)
    assert meta.message_count == 3
    assert store.get_actions(cid)[0].summary == "read a.py"


def test_list_resolve_delete_clear() -> None:
    store = HistoryStore()
    first = store.create_conversation("chat", "one")
    second = store.create_conversation("ask", "two")
    assert {c.id for c in store.list_conversations()} == {first, second}
    assert [c.id for c in store.list_conversations(kind="ask")] == [second]
    assert store.resolve_id(first[:6]) == first
    with pytest.raises(HistoryError):
        store.resolve_id("zzz")
    with pytest.raises(HistoryError):
        store.resolve_id("ffffffffffff")
    assert store.delete_conversation(first) is True
    assert store.delete_conversation(first) is False
    assert store.clear() == 1
    assert store.list_conversations() == []


def test_delete_cascades_messages() -> None:
    store = HistoryStore()
    cid = store.create_conversation("chat", "x")
    store.add_message(cid, Message.user("hi"))
    store.delete_conversation(cid)
    conn = sqlite3.connect(paths.history_db())
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        conn.close()


def test_content_is_redacted_before_storage() -> None:
    store = HistoryStore(redactor=Redactor(["super-secret-key-123"]))
    cid = store.create_conversation("chat", "my key is super-secret-key-123")
    store.add_message(cid, Message.user("use super-secret-key-123 and sk-abcdefghijklmnopqrstuvwx"))
    raw = paths.history_db().read_bytes()
    assert b"super-secret-key-123" not in raw
    assert b"sk-abcdefghijklmnopqrstuvwx" not in raw


def test_secure_delete_removes_content_from_file() -> None:
    store = HistoryStore()
    cid = store.create_conversation("chat", "x")
    store.add_message(cid, Message.user("UNIQUE-MARKER-" * 50))
    store.delete_conversation(cid)
    assert b"UNIQUE-MARKER-" not in paths.history_db().read_bytes()


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "future.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(HistoryError, match="newer"):
        HistoryStore(db).initialize()


def test_unwritable_location_is_a_history_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(HistoryError):
        HistoryStore(blocker / "history.sqlite3").initialize()


def test_database_file_can_be_deleted_after_use() -> None:
    # Windows cannot delete a file that still has an open handle.
    store = HistoryStore()
    store.create_conversation("chat", "x")
    paths.history_db().unlink()
