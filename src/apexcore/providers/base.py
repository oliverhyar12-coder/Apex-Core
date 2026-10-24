"""Provider-neutral message types and the provider interface.

Adapters translate these types to and from each service's wire format. The
types deliberately cover only what every supported service can express: text,
tool calls, and tool results. Capabilities that differ (tool support, streaming)
are declared per provider instead of assumed.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
TextCallback = Callable[[str], None]


@dataclass
class ToolCall:
    id: str
    name: str
    # The raw JSON string as produced by the model. It may be invalid JSON;
    # the agent validates it before anything runs.
    arguments: str = "{}"

    def parse_arguments(self) -> dict[str, Any]:
        text = self.arguments.strip() or "{}"
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("tool arguments must be a JSON object")
        return value

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            arguments=str(data.get("arguments", "{}")),
        )


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # For role == "tool": the id of the call this message answers.
    tool_call_id: str | None = None
    # For role == "tool": the tool name (some APIs want it).
    name: str | None = None
    # For role == "tool": whether the result is an error.
    is_error: bool = False

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str, tool_calls: list[ToolCall] | None = None) -> Message:
        return cls(role="assistant", content=content, tool_calls=list(tool_calls or []))

    @classmethod
    def tool_result(cls, call: ToolCall, content: str, *, is_error: bool = False) -> Message:
        return cls(
            role="tool", content=content, tool_call_id=call.id, name=call.name, is_error=is_error
        )


@dataclass(frozen=True)
class ToolSpec:
    """A tool as advertised to the model: a name, a description, and a JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage | None) -> Usage:
        if other is None:
            return Usage(self.input_tokens, self.output_tokens)
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass
class ChatResponse:
    message: Message
    usage: Usage | None = None
    finish_reason: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class ModelInfo:
    id: str
    description: str | None = None


class Provider(ABC):
    """A chat model service.

    ``complete`` is synchronous. When the provider streams, text deltas are
    passed to ``on_text`` as they arrive and the full response is returned at
    the end. A ``KeyboardInterrupt`` raised while waiting propagates to the
    caller after the HTTP connection is closed.
    """

    name: str
    supports_tools: bool = True
    supports_streaming: bool = True

    @abstractmethod
    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        on_text: TextCallback | None = None,
    ) -> ChatResponse: ...

    @abstractmethod
    def list_models(self) -> list[ModelInfo]: ...

    def close(self) -> None:  # noqa: B027 - optional hook, intentionally empty
        """Release network resources."""

    def cancel(self) -> None:  # noqa: B027 - optional hook, intentionally empty
        """Abort an in-flight request (called from another thread on Ctrl+C)."""

    def __enter__(self) -> Provider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
