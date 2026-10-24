"""The set of tools available to an agent session."""

from __future__ import annotations

from collections.abc import Iterable

from apexcore.tools.base import Risk, Tool
from apexcore.tools.edit import EditFileTool, WriteFileTool
from apexcore.tools.execute import RunCommandTool
from apexcore.tools.files import ListFilesTool, ReadFileTool, SearchFilesTool
from apexcore.tools.git import GitDiffTool, GitStatusTool


def all_tools() -> list[Tool]:
    return [
        ListFilesTool(),
        ReadFileTool(),
        SearchFilesTool(),
        GitStatusTool(),
        GitDiffTool(),
        EditFileTool(),
        WriteFileTool(),
        RunCommandTool(),
    ]


def known_tool_names() -> frozenset[str]:
    return frozenset(tool.name for tool in all_tools())


def build_tools(risks: Iterable[Risk]) -> list[Tool]:
    """Tools whose risk level is in ``risks`` (e.g. only READ for ``apex ask --tools``)."""
    allowed = set(risks)
    return [tool for tool in all_tools() if tool.risk in allowed]
