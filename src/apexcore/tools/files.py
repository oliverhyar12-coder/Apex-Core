"""Read-only file tools: list_files, read_file, search_files."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from pydantic import Field

from apexcore.errors import ToolError, WorkspaceError
from apexcore.tools.base import PreparedAction, Risk, Tool, ToolArgs, ToolContext, ToolResult

BINARY_SNIFF_BYTES = 8192
SEARCH_MAX_FILE_BYTES = 2_000_000
SEARCH_MAX_LINE_CHARS = 4000
# Nested quantifiers such as (a+)+ or (.*)* can take exponential time in
# Python's backtracking regex engine, and C-level matching cannot be
# interrupted with Ctrl+C. Reject the classic shapes up front.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


def looks_binary(sample: bytes) -> bool:
    return b"\x00" in sample


def read_text(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Read up to ``max_bytes`` of ``path`` as UTF-8 text. Returns (text, truncated)."""
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except PermissionError as exc:
        raise ToolError(f"Permission denied reading {path.name}.") from exc
    except OSError as exc:
        raise ToolError(f"Cannot read {path.name}: {exc.strerror or exc}") from exc
    if looks_binary(data[:BINARY_SNIFF_BYTES]):
        raise ToolError(f"{path.name} appears to be a binary file; only text files can be read.")
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8-sig", errors="replace")
    return text, truncated


class ListFilesArgs(ToolArgs):
    path: str = Field(default=".", description="Directory relative to the workspace root.")
    recursive: bool = Field(
        default=False, description="List files in subdirectories too (skips .git, venvs, etc.)."
    )
    max_entries: int = Field(default=200, ge=1, le=2000)


class ListFilesTool(Tool):
    name = "list_files"
    description = (
        "List files and directories in the workspace. Directories end with '/'. "
        "Paths are relative to the workspace root."
    )
    risk = Risk.READ
    Args = ListFilesArgs

    def prepare(self, args: ListFilesArgs, ctx: ToolContext) -> PreparedAction:
        ws = ctx.workspace
        target = ws.resolve(args.path)
        if not target.exists():
            raise ToolError(f"Directory {args.path!r} does not exist.")
        if not target.is_dir():
            raise ToolError(f"{args.path!r} is a file, not a directory; use read_file.")

        def run() -> ToolResult:
            entries: list[str] = []
            truncated = False
            if args.recursive:
                for path in ws.walk_files(target):
                    if len(entries) >= args.max_entries:
                        truncated = True
                        break
                    suffix = " -> (symlink)" if path.is_symlink() else ""
                    entries.append(ws.relative(path) + suffix)
            else:
                try:
                    children = sorted(target.iterdir(), key=lambda p: p.name.lower())
                except OSError as exc:
                    raise ToolError(f"Cannot list {args.path!r}: {exc.strerror or exc}") from exc
                for child in children:
                    if child.name.lower() == ".git":
                        continue
                    if len(entries) >= args.max_entries:
                        truncated = True
                        break
                    rel = ws.relative(child)
                    if child.is_symlink():
                        entries.append(f"{rel} -> (symlink)")
                    elif child.is_dir():
                        entries.append(f"{rel}/")
                    else:
                        entries.append(rel)
            if not entries:
                return ToolResult("(empty directory)")
            text = "\n".join(entries)
            if truncated:
                text += f"\n... (stopped after {args.max_entries} entries)"
            return ToolResult(text)

        return PreparedAction(summary=f"list {ws.relative(target)}", run=run)


class ReadFileArgs(ToolArgs):
    path: str = Field(description="File path relative to the workspace root.")
    start_line: int = Field(default=1, ge=1, description="First line to return (1-based).")
    max_lines: int = Field(default=400, ge=1, le=5000, description="Maximum lines to return.")


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a UTF-8 text file from the workspace. Output lines are prefixed with "
        "line numbers and a tab; the prefixes are not part of the file."
    )
    risk = Risk.READ
    Args = ReadFileArgs

    def prepare(self, args: ReadFileArgs, ctx: ToolContext) -> PreparedAction:
        ws = ctx.workspace
        target = ws.resolve(args.path)
        if not target.exists():
            raise ToolError(f"File {args.path!r} does not exist.")
        if target.is_dir():
            raise ToolError(f"{args.path!r} is a directory; use list_files.")
        ws.check_not_secret(target)

        def run() -> ToolResult:
            text, truncated = read_text(target, ctx.tool_settings.max_read_bytes)
            lines = text.splitlines()
            start = args.start_line - 1
            selected = lines[start : start + args.max_lines]
            if not lines:
                return ToolResult(f"{ws.relative(target)} is empty.")
            if not selected:
                raise ToolError(
                    f"start_line {args.start_line} is past the end ({len(lines)} lines)."
                )
            end = start + len(selected)
            body = "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(selected, start + 1))
            header = f"{ws.relative(target)} (lines {start + 1}-{end} of {len(lines)}"
            header += ", file truncated at size limit)" if truncated else ")"
            if end < len(lines):
                body += f"\n... ({len(lines) - end} more lines; use start_line={end + 1})"
            return ToolResult(f"{header}\n{ctx.redactor.redact(body)}")

        return PreparedAction(summary=f"read {ws.relative(target)}", run=run)


class SearchFilesArgs(ToolArgs):
    query: str = Field(min_length=1, description="Text (or regular expression) to search for.")
    path: str = Field(default=".", description="Directory to search, relative to the root.")
    regex: bool = Field(default=False, description="Treat query as a Python regular expression.")
    case_sensitive: bool = False
    glob: str = Field(
        default="", description="Only search files matching this name glob, e.g. *.py"
    )
    max_results: int = Field(default=100, ge=1, le=1000)


class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Search text files in the workspace for a string or regex. Returns "
        "'path:line: text' matches. Skips binary, very large, and credentials files."
    )
    risk = Risk.READ
    Args = SearchFilesArgs

    def prepare(self, args: SearchFilesArgs, ctx: ToolContext) -> PreparedAction:
        ws = ctx.workspace
        start = ws.resolve(args.path)
        if not start.is_dir():
            raise ToolError(f"{args.path!r} is not a directory.")
        flags = 0 if args.case_sensitive else re.IGNORECASE
        if args.regex:
            if _NESTED_QUANTIFIER.search(args.query):
                raise ToolError(
                    "Regex rejected: nested quantifiers like (a+)+ can hang the search. "
                    "Simplify the pattern."
                )
            try:
                pattern = re.compile(args.query, flags)
            except re.error as exc:
                raise ToolError(f"Invalid regular expression: {exc}") from exc
        else:
            pattern = re.compile(re.escape(args.query), flags)

        def run() -> ToolResult:
            results: list[str] = []
            files_scanned = 0
            for path in ws.walk_files(start):
                if args.glob and not fnmatch.fnmatch(path.name.lower(), args.glob.lower()):
                    continue
                try:
                    real = ws.resolve(path)
                    if ws.is_secret(real) or not real.is_file():
                        continue
                    if real.stat().st_size > SEARCH_MAX_FILE_BYTES:
                        continue
                    text, _ = read_text(real, SEARCH_MAX_FILE_BYTES)
                except (WorkspaceError, ToolError, OSError):
                    continue
                files_scanned += 1
                for number, line in enumerate(text.splitlines(), 1):
                    if len(line) > SEARCH_MAX_LINE_CHARS:
                        continue
                    if pattern.search(line):
                        shown = line.strip()
                        shown = shown if len(shown) <= 300 else shown[:300] + "..."
                        results.append(f"{ws.relative(path)}:{number}: {shown}")
                        if len(results) >= args.max_results:
                            body = "\n".join(results)
                            note = f"\n... (stopped after {args.max_results} matches)"
                            return ToolResult(ctx.redactor.redact(body + note))
            if not results:
                return ToolResult(f"No matches in {files_scanned} files.")
            return ToolResult(ctx.redactor.redact("\n".join(results)))

        return PreparedAction(summary=f"search {args.query!r} in {ws.relative(start)}", run=run)
