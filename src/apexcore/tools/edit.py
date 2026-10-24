"""File-editing tools: write_file and edit_file.

Both show a unified diff before anything changes, and require approval under
the default permission settings. Writes are atomic (temp file + rename), keep
the file's existing line-ending style and permission bits, and are refused if
the file changed between the preview and the approval.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import Field

from apexcore.errors import ToolError
from apexcore.redaction import REDACTED
from apexcore.tools.base import PreparedAction, Risk, Tool, ToolArgs, ToolContext, ToolResult
from apexcore.tools.files import looks_binary
from apexcore.tools.workspace import Workspace

MAX_EDIT_BYTES = 5_000_000
MAX_DIFF_LINES = 400


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _read_existing(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ToolError(f"{path.name} exists and is not a regular file.")
    if path.stat().st_size > MAX_EDIT_BYTES:
        raise ToolError(f"{path.name} is too large to edit safely (> {MAX_EDIT_BYTES:,} bytes).")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"Cannot read {path.name}: {exc.strerror or exc}") from exc
    if looks_binary(data[:8192]):
        raise ToolError(f"{path.name} is a binary file; refusing to edit it.")
    return data


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("File is not valid UTF-8; refusing to edit it.") from exc


def make_diff(old: str, new: str, rel: str) -> str:
    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel}" if old else "/dev/null",
            tofile=f"b/{rel}",
            n=3,
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        omitted = len(lines) - MAX_DIFF_LINES
        lines = [*lines[:MAX_DIFF_LINES], f"... ({omitted} more diff lines)\n"]
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _diff_stats(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def _new_file_mode() -> int:
    """The mode a normally created file would get (0o666 minus the umask)."""
    umask = os.umask(0o022)
    os.umask(umask)
    return 0o666 & ~umask


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".apex-tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates owner-only files; keep the existing file's mode, or
        # give a new file the same mode any editor would.
        with contextlib.suppress(OSError):
            if path.exists():
                shutil.copymode(path, tmp)
            elif os.name == "posix":
                tmp.chmod(_new_file_mode())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _check_target(ws: Workspace, raw: str) -> Path:
    target = ws.resolve(raw)
    if target == ws.root:
        raise ToolError("Path must name a file, not the workspace root.")
    ws.check_not_secret(target)
    return target


def _commit(ws: Workspace, target: Path, expected: str | None, new_data: bytes) -> None:
    # Re-validate right before writing: the path must still be inside the
    # workspace and the file must still match what the user approved.
    again = ws.resolve(ws.relative(target))
    if again != target:
        raise ToolError("The path changed since the preview (symlink?); refusing to write.")
    current = _digest(target.read_bytes()) if target.is_file() else None
    if current != expected:
        raise ToolError(
            f"{ws.relative(target)} changed since the preview; refusing to overwrite. "
            "Read it again and retry."
        )
    try:
        _write_atomic(target, new_data)
    except OSError as exc:
        raise ToolError(f"Could not write {ws.relative(target)}: {exc.strerror or exc}") from exc


def _match_newlines(original: str | None, text: str) -> str:
    if original and "\r\n" in original and "\r\n" not in text:
        return text.replace("\n", "\r\n")
    return text


class WriteFileArgs(ToolArgs):
    path: str = Field(description="File path relative to the workspace root.")
    content: str = Field(description="The complete new file content.")


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file or replace an existing file's entire content. Prefer "
        "edit_file for changes to existing files. Requires user approval."
    )
    risk = Risk.WRITE
    Args = WriteFileArgs

    def prepare(self, args: WriteFileArgs, ctx: ToolContext) -> PreparedAction:
        ws = ctx.workspace
        target = _check_target(ws, args.path)
        old_data = _read_existing(target)
        old = _decode(old_data) if old_data is not None else ""
        if REDACTED in args.content and REDACTED not in old:
            raise ToolError(
                f"The new content contains the placeholder {REDACTED}, which marks a secret "
                "that was hidden from you. Writing it would destroy the real value; edit "
                "around that region instead."
            )
        new = _match_newlines(old if old_data is not None else None, args.content)
        rel = ws.relative(target)
        if old_data is not None and old == new:
            raise ToolError(f"{rel} already has exactly this content; nothing to do.")
        added, removed = _diff_stats(old, new)
        if old_data is None:
            summary = f"create {rel} ({len(new.splitlines())} lines)"
        else:
            summary = f"overwrite {rel} (+{added} -{removed})"
        expected = _digest(old_data)

        def run() -> ToolResult:
            _commit(ws, target, expected, new.encode("utf-8"))
            return ToolResult(f"Wrote {rel} ({len(new.encode('utf-8')):,} bytes).")

        return PreparedAction(summary=summary, details=make_diff(old, new, rel), run=run)


class EditFileArgs(ToolArgs):
    path: str = Field(description="Existing file path relative to the workspace root.")
    old_text: str = Field(
        min_length=1,
        description="Exact text to replace, copied from the file without line-number prefixes. "
        "Include enough context to make it unique.",
    )
    new_text: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence.")


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact piece of text in an existing file. Fails if the text is "
        "missing or (unless replace_all) appears more than once. Requires user approval."
    )
    risk = Risk.WRITE
    Args = EditFileArgs

    def prepare(self, args: EditFileArgs, ctx: ToolContext) -> PreparedAction:
        ws = ctx.workspace
        target = _check_target(ws, args.path)
        rel = ws.relative(target)
        old_data = _read_existing(target)
        if old_data is None:
            raise ToolError(f"{rel} does not exist; use write_file to create it.")
        if REDACTED in args.old_text or REDACTED in args.new_text:
            raise ToolError(
                f"The edit contains the placeholder {REDACTED}, which hides a secret; "
                "edit a different region of the file."
            )
        old = _decode(old_data)
        old_text = _match_newlines(old, args.old_text)
        new_text = _match_newlines(old, args.new_text)
        count = old.count(old_text)
        if count == 0:
            raise ToolError(
                f"old_text was not found in {rel}. Read the file again and copy the text "
                "exactly (whitespace and indentation matter; omit line-number prefixes)."
            )
        if count > 1 and not args.replace_all:
            raise ToolError(
                f"old_text appears {count} times in {rel}. Add surrounding context to make "
                "it unique, or set replace_all=true."
            )
        new = (
            old.replace(old_text, new_text)
            if args.replace_all
            else old.replace(old_text, new_text, 1)
        )
        if new == old:
            raise ToolError("The replacement leaves the file unchanged.")
        added, removed = _diff_stats(old, new)
        expected = _digest(old_data)
        occurrences = f", {count} occurrences" if args.replace_all and count > 1 else ""

        def run() -> ToolResult:
            _commit(ws, target, expected, new.encode("utf-8"))
            return ToolResult(f"Edited {rel} (+{added} -{removed}{occurrences}).")

        return PreparedAction(
            summary=f"edit {rel} (+{added} -{removed}{occurrences})",
            details=make_diff(old, new, rel),
            run=run,
        )
