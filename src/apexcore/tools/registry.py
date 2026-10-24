"""The set of tools available to an agent session."""

from __future__ import annotations

from collections.abc import Iterable

from apexcore.tools.base import Risk, Tool
from apexcore.tools.edit import EditFileTool, WriteFileTool
from apexcore.tools.execute import RunCommandTool
from apexcore.tools.files import ListFilesTool, ReadFileTool, SearchFilesTool
from apexcore.tools.git import GitDiffTool, GitStatusTool
from apexcore.tools.memory_tool import MemoryTool
from apexcore.tools.todo import TodoTool

# Tools that only make sense in an ongoing conversation (not a one-shot `ask`).
SESSION_TOOLS = frozenset({"todo", "memory"})


def all_tools() -> list[Tool]:
    return [
        ListFilesTool(),
        ReadFileTool(),
        SearchFilesTool(),
        GitStatusTool(),
        GitDiffTool(),
        TodoTool(),
        MemoryTool(),
        EditFileTool(),
        WriteFileTool(),
        RunCommandTool(),
    ]


def known_tool_names() -> frozenset[str]:
    return frozenset(tool.name for tool in all_tools())


def build_tools(risks: Iterable[Risk], *, session_tools: bool = True) -> list[Tool]:
    """Tools whose risk level is in ``risks``. ``session_tools=False`` leaves out the
    plan and memory tools (used for one-shot `apex ask --tools`)."""
    allowed = set(risks)
    return [
        tool
        for tool in all_tools()
        if tool.risk in allowed and (session_tools or tool.name not in SESSION_TOOLS)
    ]
