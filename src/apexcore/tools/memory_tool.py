"""The memory tool: lets the agent keep short, durable notes across sessions."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from apexcore.errors import ToolError
from apexcore.memory import MemoryChange
from apexcore.tools.base import PreparedAction, Risk, Tool, ToolArgs, ToolContext, ToolResult


class MemoryArgs(ToolArgs):
    action: Literal["add", "patch", "remove"] = Field(
        description="add a new entry, patch (edit) part of one, or remove one."
    )
    target: Literal["memory", "user"] = Field(
        description="'user' for facts about the user (preferences, style, role); 'memory' for "
        "your own notes (environment, projects, conventions, lessons learned)."
    )
    content: str = Field(default="", description="New entry text (add) or replacement (patch).")
    old_text: str = Field(
        default="", description="A unique snippet of the existing entry (patch and remove)."
    )


class MemoryTool(Tool):
    name = "memory"
    description = (
        "Save durable notes that you will see again in future sessions. Use it for stable "
        "facts worth remembering (user preferences, how this machine or project works, "
        "lessons from mistakes), not for temporary task details. Keep entries short; never "
        "store secrets. Memory is small: merge or remove old entries when it fills up."
    )
    risk = Risk.MEMORY
    Args = MemoryArgs

    def prepare(self, args: MemoryArgs, ctx: ToolContext) -> PreparedAction:
        store = ctx.memory
        if store is None:
            raise ToolError("Memory is disabled in this session.")
        if args.action == "add" and not args.content.strip():
            raise ToolError("add requires content.")
        if args.action in ("patch", "remove") and not args.old_text.strip():
            raise ToolError(f"{args.action} requires old_text to find the entry.")
        if args.action == "patch" and not args.content.strip():
            raise ToolError("patch requires content (the replacement text).")

        def run() -> ToolResult:
            change: MemoryChange
            if args.action == "add":
                change = store.add(args.target, args.content)
            elif args.action == "patch":
                change = store.patch(args.target, args.old_text, args.content)
            else:
                change = store.remove(args.target, args.old_text)
            return ToolResult(change.describe() + ". It will be in your prompt next session.")

        preview = (args.content or args.old_text).strip().replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:59] + "…"
        return PreparedAction(summary=f"{args.action} {args.target} memory: {preview}", run=run)
