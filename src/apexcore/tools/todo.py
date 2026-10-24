"""The todo tool: a visible plan the agent keeps up to date during a task."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from apexcore.tools.base import (
    PreparedAction,
    Risk,
    TodoItem,
    Tool,
    ToolArgs,
    ToolContext,
    ToolResult,
)

MARKS = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}


class TodoEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=200, description="What needs doing.")
    status: Literal["pending", "in_progress", "completed"] = "pending"


class TodoArgs(ToolArgs):
    items: list[TodoEntry] = Field(
        max_length=30,
        description="The COMPLETE updated plan (this replaces the previous list). Keep exactly "
        "one item in_progress while you work.",
    )


def render_todos(items: list[TodoItem]) -> str:
    if not items:
        return "(no plan)"
    return "\n".join(f"{MARKS.get(item.status, '[ ]')} {item.content}" for item in items)


class TodoTool(Tool):
    name = "todo"
    description = (
        "Keep a short plan for multi-step tasks and show your progress to the user. Pass the "
        "whole list every time; mark items completed as you finish them."
    )
    risk = Risk.READ
    Args = TodoArgs

    def prepare(self, args: TodoArgs, ctx: ToolContext) -> PreparedAction:
        done = sum(1 for item in args.items if item.status == "completed")

        def run() -> ToolResult:
            ctx.todos[:] = [TodoItem(item.content, item.status) for item in args.items]
            text = render_todos(ctx.todos)
            return ToolResult(
                f"Plan updated ({done}/{len(args.items)} done):\n{text}", display=text
            )

        return PreparedAction(summary=f"plan {done}/{len(args.items)} done", run=run)
