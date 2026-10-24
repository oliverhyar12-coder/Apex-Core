"""Persistent memory across sessions.

Two small Markdown files in the data directory (``memories/``):

* ``MEMORY.md``: the agent's own notes (environment, project conventions,
  lessons learned);
* ``USER.md``: what the agent has learned about you (preferences, style).

Entries are separated by a line containing only ``§``. The files are plain
text you can read and edit (``apex memory edit``). Both have hard size limits,
so memory stays small and cheap: a write that would exceed the limit is
refused and the agent has to consolidate first.

Memory is loaded into the system prompt once, when a conversation starts.
Changes made during a conversation take effect in the next one.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from apexcore import paths
from apexcore.errors import ToolError
from apexcore.redaction import GLOBAL_REDACTOR, Redactor
from apexcore.safety import scan_text

Target = Literal["memory", "user"]
TARGETS: tuple[Target, ...] = ("memory", "user")
FILES: dict[Target, str] = {"memory": "MEMORY.md", "user": "USER.md"}
LIMITS: dict[Target, int] = {"memory": 2200, "user": 1375}
LABELS: dict[Target, str] = {
    "memory": "Your notes (environment, projects, conventions, lessons)",
    "user": "About the user (preferences, style, context)",
}
DELIMITER = "\n§\n"
MAX_ENTRY_CHARS = 600


def memory_dir() -> Path:
    return paths.data_dir() / "memories"


@dataclass(frozen=True)
class MemoryChange:
    target: Target
    action: str
    preview: str
    used: int
    limit: int

    def describe(self) -> str:
        return (
            f"{self.action} {self.target} memory: {self.preview} ({self.used}/{self.limit} chars)"
        )


class MemoryStore:
    def __init__(self, directory: Path | None = None, redactor: Redactor | None = None) -> None:
        self.directory = directory or memory_dir()
        self.redactor = redactor or GLOBAL_REDACTOR

    # --- Reading ---------------------------------------------------------

    def path(self, target: Target) -> Path:
        return self.directory / FILES[target]

    def entries(self, target: Target) -> list[str]:
        try:
            text = self.path(target).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        parts = (part.strip() for part in text.replace("\r\n", "\n").split(DELIMITER))
        return [part for part in parts if part and part != "§"]

    def used(self, target: Target) -> int:
        return len(DELIMITER.join(self.entries(target)))

    def is_empty(self) -> bool:
        return not any(self.entries(t) for t in TARGETS)

    def render_for_prompt(self) -> str:
        """The block inserted into the system prompt (empty if there is no memory)."""
        blocks = []
        for target in TARGETS:
            entries = self.entries(target)
            if not entries:
                continue
            body = DELIMITER.join(entries)
            blocks.append(f"### {LABELS[target]} [{len(body)}/{LIMITS[target]} chars]\n{body}")
        return "\n\n".join(blocks)

    # --- Writing ---------------------------------------------------------

    def _save(self, target: Target, entries: list[str]) -> None:
        paths.ensure_private_dir(self.directory)
        text = DELIMITER.join(entries) + ("\n" if entries else "")
        paths.atomic_write_text(self.path(target), text, private=True)

    def _check_content(self, content: str) -> str:
        content = content.strip().replace("\r\n", "\n")
        if not content:
            raise ToolError("Memory content is empty.")
        if "§" in content:
            raise ToolError("Memory content must not contain the '§' delimiter.")
        if len(content) > MAX_ENTRY_CHARS:
            raise ToolError(
                f"Memory entries must be short (max {MAX_ENTRY_CHARS} characters); summarize it."
            )
        if self.redactor.redact(content) != content:
            raise ToolError("Refusing to store something that looks like a secret or API key.")
        findings = scan_text(content)
        if findings:
            raise ToolError(
                "Refusing to store this in memory: it looks like a prompt-injection attempt "
                f"({', '.join(findings)})."
            )
        return content

    def _check_limit(self, target: Target, entries: list[str]) -> int:
        used = len(DELIMITER.join(entries))
        if used > LIMITS[target]:
            raise ToolError(
                f"The {target} memory would exceed its limit ({used}/{LIMITS[target]} chars). "
                "Remove or merge older entries (patch/remove) first."
            )
        return used

    def _find(self, entries: list[str], old_text: str) -> int:
        old_text = old_text.strip()
        if not old_text:
            raise ToolError("old_text is required to identify the entry.")
        matches = [i for i, entry in enumerate(entries) if old_text in entry]
        if not matches:
            raise ToolError("No memory entry contains that old_text.")
        if len(matches) > 1:
            raise ToolError("old_text matches several entries; use a longer, unique snippet.")
        return matches[0]

    def add(self, target: Target, content: str) -> MemoryChange:
        content = self._check_content(content)
        entries = self.entries(target)
        if content in entries:
            return MemoryChange(target, "kept", "already saved, no duplicate added",
                                self.used(target), LIMITS[target])  # fmt: skip
        entries.append(content)
        used = self._check_limit(target, entries)
        self._save(target, entries)
        return MemoryChange(target, "added", _preview(content), used, LIMITS[target])

    def patch(self, target: Target, old_text: str, content: str) -> MemoryChange:
        entries = self.entries(target)
        index = self._find(entries, old_text)
        if entries[index].count(old_text.strip()) > 1:
            raise ToolError("old_text appears more than once in that entry; be more specific.")
        updated = entries[index].replace(old_text.strip(), content.strip(), 1)
        entries[index] = self._check_content(updated)
        used = self._check_limit(target, entries)
        self._save(target, entries)
        return MemoryChange(target, "updated", _preview(entries[index]), used, LIMITS[target])

    def remove(self, target: Target, old_text: str) -> MemoryChange:
        entries = self.entries(target)
        index = self._find(entries, old_text)
        removed = entries.pop(index)
        self._save(target, entries)
        return MemoryChange(target, "removed", _preview(removed), self.used(target), LIMITS[target])

    def clear(self, target: Target | None = None) -> None:
        for name in [target] if target else list(TARGETS):
            with contextlib.suppress(FileNotFoundError):
                self.path(name).unlink()


def _preview(text: str, limit: int = 70) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
