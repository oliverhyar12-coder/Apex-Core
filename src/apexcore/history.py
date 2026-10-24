"""Local conversation and task history in SQLite.

The database lives in the per-user data directory (see :mod:`apexcore.paths`).
Nothing in it is ever uploaded. Content is passed through the redactor before
it is written, and ``secure_delete`` is enabled so deleted rows are overwritten
on disk rather than left in free pages.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from apexcore import paths
from apexcore.errors import HistoryError
from apexcore.providers.base import Message, ToolCall, Usage
from apexcore.redaction import GLOBAL_REDACTOR, Redactor

SCHEMA_VERSION = 1

Kind = Literal["chat", "ask", "task"]
Status = Literal["active", "completed", "cancelled", "failed", "max_steps"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('chat', 'ask', 'task')),
    title TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    workspace TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    name TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    tool TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_conversation ON actions(conversation_id, id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    kind: str
    title: str
    provider: str | None
    model: str | None
    workspace: str | None
    status: str
    created_at: str
    updated_at: str
    input_tokens: int
    output_tokens: int
    message_count: int


@dataclass(frozen=True)
class ActionEntry:
    tool: str
    summary: str
    status: str
    created_at: str


class HistoryStore:
    """Thin wrapper around the SQLite file. Opens a short-lived connection per
    operation so no file handle is left open (Windows cannot delete open files)."""

    def __init__(self, path: Path | None = None, redactor: Redactor | None = None) -> None:
        self.path = path or paths.history_db()
        self.redactor = redactor or GLOBAL_REDACTOR
        self._initialized = False

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            paths.ensure_private_dir(self.path.parent)
            conn = sqlite3.connect(self.path, timeout=5.0)
        except (OSError, sqlite3.Error) as exc:
            raise HistoryError(
                f"Cannot open history database {self.path}: {exc}",
                hint="Check permissions, or set APEX_DATA_DIR to a writable directory.",
            ) from exc
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA secure_delete = ON")
            if not self._initialized:
                self._migrate(conn)
                self._initialized = True
            with conn:
                yield conn
        except sqlite3.DatabaseError as exc:
            raise HistoryError(f"History database error ({self.path}): {exc}") from exc
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise HistoryError(
                f"History database {self.path} was created by a newer APEX CORE "
                f"(schema {version}, this version supports {SCHEMA_VERSION})."
            )
        if version < SCHEMA_VERSION:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        paths.restrict_file(self.path)

    def initialize(self) -> int:
        """Create the database if needed and return its schema version."""
        with self._connect() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    # --- Writing ---------------------------------------------------------

    def create_conversation(
        self,
        kind: Kind,
        title: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> str:
        conversation_id = uuid.uuid4().hex[:12]
        now = _now()
        title = self.redactor.redact(" ".join(title.split()))[:120] or "(untitled)"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, kind, title, provider, model, workspace, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, kind, title, provider, model, workspace, now, now),
            )
        return conversation_id

    def add_message(self, conversation_id: str, message: Message) -> int:
        """Store a message and return its row id."""
        tool_calls = (
            json.dumps([call.to_dict() for call in message.tool_calls])
            if message.tool_calls
            else None
        )
        now = _now()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_calls, "
                "tool_call_id, name, is_error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    message.role,
                    self.redactor.redact(message.content or ""),
                    self.redactor.redact(tool_calls) if tool_calls else None,
                    message.tool_call_id,
                    message.name,
                    int(message.is_error),
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
            )
            return int(cursor.lastrowid or 0)

    def delete_messages(self, message_ids: list[int]) -> None:
        """Remove messages (used by /undo and /retry)."""
        if not message_ids:
            return
        with self._connect() as conn:
            conn.executemany("DELETE FROM messages WHERE id = ?", [(i,) for i in message_ids])

    def rename(self, conversation_id: str, title: str) -> str:
        title = self.redactor.redact(" ".join(title.split()))[:120]
        if not title:
            raise HistoryError("The title is empty.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
            )
        return title

    def latest(
        self, kind: str = "chat", workspace: str | None = None
    ) -> ConversationSummary | None:
        """The most recently updated conversation, preferring this workspace."""
        candidates = self.list_conversations(limit=200, kind=kind)
        if workspace is not None:
            for summary in candidates:
                if summary.workspace == workspace and summary.message_count:
                    return summary
        return next((c for c in candidates if c.message_count), None)

    def resolve_ref(self, ref: str) -> str:
        """An id prefix or a title (exact, then unique prefix; case-insensitive)."""
        ref = ref.strip()
        if ref and all(c in "0123456789abcdef" for c in ref.lower()):
            with contextlib.suppress(HistoryError):
                return self.resolve_id(ref)
        lowered = ref.lower()
        titles = self.list_conversations(limit=1000)
        exact = [c for c in titles if c.title.lower() == lowered]
        if exact:
            return exact[0].id
        prefixed = [c for c in titles if c.title.lower().startswith(lowered)]
        if len(prefixed) == 1:
            return prefixed[0].id
        if len(prefixed) > 1:
            raise HistoryError(f"{ref!r} matches several conversations; use the id.")
        raise HistoryError(
            f"No conversation with id or title {ref!r}.", hint="Run `apex history` to list them."
        )

    def add_action(self, conversation_id: str, tool: str, summary: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO actions (conversation_id, tool, summary, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, tool, self.redactor.redact(summary)[:500], status, _now()),
            )

    def update_conversation(
        self,
        conversation_id: str,
        *,
        status: Status | None = None,
        usage: Usage | None = None,
        model: str | None = None,
    ) -> None:
        with self._connect() as conn:
            if status is not None:
                conn.execute(
                    "UPDATE conversations SET status = ? WHERE id = ?", (status, conversation_id)
                )
            if usage is not None:
                conn.execute(
                    "UPDATE conversations SET input_tokens = input_tokens + ?, "
                    "output_tokens = output_tokens + ? WHERE id = ?",
                    (usage.input_tokens, usage.output_tokens, conversation_id),
                )
            if model is not None:
                conn.execute(
                    "UPDATE conversations SET model = ? WHERE id = ?", (model, conversation_id)
                )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conversation_id)
            )

    # --- Reading ---------------------------------------------------------

    def list_conversations(
        self, limit: int = 20, kind: str | None = None
    ) -> list[ConversationSummary]:
        query = (
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) "
            "AS message_count FROM conversations c"
        )
        params: list[object] = []
        if kind:
            query += " WHERE c.kind = ?"
            params.append(kind)
        query += " ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [_summary(row) for row in conn.execute(query, params)]

    def resolve_id(self, prefix: str) -> str:
        """Expand a unique id prefix (as shown by ``apex history``) to a full id."""
        prefix = prefix.strip().lower()
        if not prefix or not all(c in "0123456789abcdef" for c in prefix):
            raise HistoryError(f"Invalid conversation id {prefix!r}.")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM conversations WHERE id LIKE ? ORDER BY id LIMIT 2",
                (prefix + "%",),
            ).fetchall()
        if not rows:
            raise HistoryError(
                f"No conversation with id {prefix!r}.", hint="Run `apex history` to list ids."
            )
        if len(rows) > 1:
            raise HistoryError(f"Id prefix {prefix!r} is ambiguous; type more characters.")
        return str(rows[0]["id"])

    def get_conversation(self, conversation_id: str) -> ConversationSummary:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) "
                "AS message_count FROM conversations c WHERE c.id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise HistoryError(f"No conversation with id {conversation_id!r}.")
        return _summary(row)

    def get_messages(self, conversation_id: str) -> list[Message]:
        return [message for _, message in self.get_messages_with_ids(conversation_id)]

    def get_messages_with_ids(self, conversation_id: str) -> list[tuple[int, Message]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        messages: list[tuple[int, Message]] = []
        for row in rows:
            calls = []
            if row["tool_calls"]:
                with contextlib.suppress(ValueError, TypeError):
                    calls = [ToolCall.from_dict(item) for item in json.loads(row["tool_calls"])]
            messages.append(
                (
                    int(row["id"]),
                    Message(
                        role=row["role"],
                        content=row["content"],
                        tool_calls=calls,
                        tool_call_id=row["tool_call_id"],
                        name=row["name"],
                        is_error=bool(row["is_error"]),
                    ),
                )
            )
        return messages

    def get_actions(self, conversation_id: str) -> list[ActionEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tool, summary, status, created_at FROM actions "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [ActionEntry(r["tool"], r["summary"], r["status"], r["created_at"]) for r in rows]

    # --- Deleting --------------------------------------------------------

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            return cursor.rowcount > 0

    def clear(self) -> int:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            conn.execute("DELETE FROM conversations")
        # VACUUM cannot run inside a transaction; it rewrites the file so no
        # deleted content remains in unused pages.
        try:
            conn = sqlite3.connect(self.path, timeout=5.0)
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise HistoryError(f"History cleared but VACUUM failed: {exc}") from exc
        return int(count)


def _summary(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        provider=row["provider"],
        model=row["model"],
        workspace=row["workspace"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        message_count=row["message_count"],
    )
