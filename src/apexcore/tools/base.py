"""Tool framework.

A tool call goes through three stages:

1. **validate**: the model's JSON arguments are checked against the tool's
   Pydantic model; invalid calls are returned to the model as errors.
2. **prepare**: the tool checks paths and preconditions and builds a
   :class:`PreparedAction` describing exactly what will happen (a diff, a
   command line). Nothing has been changed yet.
3. **execute**: only after the permission policy (and, if required, the user)
   approves the prepared action.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from apexcore.config import PermissionSettings, ToolSettings
from apexcore.errors import ToolError
from apexcore.providers.base import ToolSpec
from apexcore.redaction import GLOBAL_REDACTOR, Redactor
from apexcore.tools.workspace import Workspace


class Risk(enum.StrEnum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class ToolResult:
    content: str
    ok: bool = True


@dataclass
class ToolContext:
    workspace: Workspace
    tool_settings: ToolSettings = field(default_factory=ToolSettings)
    permission_settings: PermissionSettings = field(default_factory=PermissionSettings)
    redactor: Redactor = field(default_factory=lambda: GLOBAL_REDACTOR)
    # Secret values to strip from child-process environments (provider keys).
    secret_env_names: frozenset[str] = frozenset()


@dataclass
class PreparedAction:
    """A validated, not-yet-executed tool action."""

    summary: str
    run: Callable[[], ToolResult]
    # Extra detail shown when asking for approval (a diff, a full command line).
    details: str = ""
    warnings: list[str] = field(default_factory=list)
    # Execute actions: the argv, used for auto-approval prefix matching.
    argv: list[str] | None = None
    # False if this action must always be confirmed, even when auto-approval
    # rules would otherwise match (destructive or opaque commands).
    auto_approvable: bool = True


def _clean_schema(node: Any) -> Any:
    """Strip Pydantic's ``title`` keys, which some providers reject or ignore."""
    if isinstance(node, dict):
        return {k: _clean_schema(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    return node


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    risk: ClassVar[Risk]
    Args: ClassVar[type[ToolArgs]]

    def spec(self) -> ToolSpec:
        schema = _clean_schema(self.Args.model_json_schema())
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return ToolSpec(name=self.name, description=self.description, parameters=schema)

    def parse_args(self, raw: dict[str, Any]) -> ToolArgs:
        try:
            return self.Args.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ToolError(f"Invalid arguments for {self.name}: {problems}") from None

    @abstractmethod
    def prepare(self, args: Any, ctx: ToolContext) -> PreparedAction: ...


def truncate(text: str, limit: int, *, what: str = "output") -> str:
    """Keep the head and tail of ``text`` within ``limit`` characters."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [{omitted:,} characters of {what} omitted] ...\n{text[-tail:]}"
