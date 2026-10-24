"""Adapter for Anthropic's native Messages API.

Differences from the OpenAI format that this adapter handles:

* the system prompt is a top-level ``system`` field, not a message;
* tool calls are ``tool_use`` content blocks with an already-parsed ``input``;
* tool results are ``tool_result`` blocks inside a *user* message;
* ``max_tokens`` is required;
* authentication uses ``x-api-key`` plus an ``anthropic-version`` header;
* streaming uses typed SSE events (``content_block_delta`` ...).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from apexcore.errors import ProviderError, ProviderResponseError, ProviderUnavailableError
from apexcore.providers.base import (
    ChatResponse,
    Message,
    ModelInfo,
    Provider,
    TextCallback,
    ToolCall,
    ToolSpec,
    Usage,
)
from apexcore.providers.http import HTTPProviderMixin, iter_sse, load_event_json
from apexcore.redaction import Redactor

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 8192


def _tool_input(call: ToolCall) -> dict[str, Any]:
    try:
        return call.parse_arguments()
    except ValueError:
        return {}


def to_anthropic_messages(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(system, messages)`` in Anthropic format.

    Consecutive messages with the same role are merged, because the API expects
    user and assistant turns to alternate.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def append(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for msg in messages:
        if msg.role == "system":
            if msg.content:
                system_parts.append(msg.content)
        elif msg.role == "user":
            append("user", [{"type": "text", "text": msg.content}] if msg.content else [])
        elif msg.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for call in msg.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": _tool_input(call),
                    }
                )
            append("assistant", blocks)
        elif msg.role == "tool":
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": msg.content or "(no output)",
            }
            if msg.is_error:
                block["is_error"] = True
            append("user", [block])
    return "\n\n".join(system_parts), out


def to_anthropic_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
        for tool in tools
    ]


def _usage(data: Any) -> Usage:
    if not isinstance(data, dict):
        return Usage()
    input_tokens = sum(
        int(data.get(key) or 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return Usage(input_tokens=input_tokens, output_tokens=int(data.get("output_tokens") or 0))


class AnthropicProvider(HTTPProviderMixin, Provider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None,
        api_key_env: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        stream: bool = True,
        supports_tools: bool = True,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        headers = {"Content-Type": "application/json", "anthropic-version": API_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        self._init_http(
            name=name,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
            api_key_env=api_key_env,
            redactor=redactor,
        )
        self.supports_streaming = stream
        self.supports_tools = supports_tools
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens or DEFAULT_MAX_TOKENS

    def map_http_error(self, response: Any) -> ProviderError:
        # 529 is Anthropic's "overloaded" status.
        if response.status_code == 529:
            return ProviderUnavailableError(
                f"{self.name}: HTTP 529: the API is temporarily overloaded.",
                hint="Try again shortly.",
            )
        return super().map_http_error(response)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        on_text: TextCallback | None = None,
    ) -> ChatResponse:
        system, wire_messages = to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_output_tokens,
            "messages": wire_messages,
        }
        if system:
            payload["system"] = system
        if tools and self.supports_tools:
            payload["tools"] = to_anthropic_tools(tools)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        stream = self.supports_streaming and on_text is not None
        if not stream:
            with self.request("POST", "messages", json_body=payload) as response:
                data = self.parse_json(response)
            result = self._parse_message(data, model)
            if on_text and result.message.content:
                on_text(result.message.content)
            return result
        payload["stream"] = True
        with self.request("POST", "messages", json_body=payload, stream=True) as response:
            return self._parse_stream(response, model, on_text)

    def _parse_message(self, data: Any, model: str) -> ChatResponse:
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise ProviderResponseError(f"{self.name}: unexpected response shape (no content).")
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data["content"]:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"toolu_{len(calls)}"),
                        name=str(block.get("name") or ""),
                        arguments=json.dumps(block.get("input") or {}),
                    )
                )
        return ChatResponse(
            message=Message.assistant("".join(text_parts), calls),
            usage=_usage(data.get("usage")),
            finish_reason=data.get("stop_reason"),
            model=data.get("model") or model,
        )

    def _parse_stream(
        self, response: Any, model: str, on_text: TextCallback | None
    ) -> ChatResponse:
        blocks: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish_reason: str | None = None
        served_model = model
        for event, data in iter_sse(response, self):
            payload = load_event_json(data, self)
            kind = payload.get("type") or event
            if kind == "error":
                err = payload.get("error") or {}
                message = self._clean(str(err.get("message") or err))
                if err.get("type") == "overloaded_error":
                    raise ProviderUnavailableError(f"{self.name}: overloaded: {message}")
                raise ProviderError(f"{self.name}: stream error: {message}")
            if kind == "message_start":
                message = payload.get("message") or {}
                served_model = message.get("model") or served_model
                usage = usage + _usage(message.get("usage"))
            elif kind == "content_block_start":
                block = dict(payload.get("content_block") or {})
                block["_json"] = ""
                blocks[int(payload.get("index", len(blocks)))] = block
            elif kind == "content_block_delta":
                index = int(payload.get("index", 0))
                block = blocks.setdefault(index, {"type": "text", "text": "", "_json": ""})
                delta = payload.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text = str(delta.get("text") or "")
                    block["text"] = str(block.get("text") or "") + text
                    if on_text and text:
                        on_text(text)
                elif delta.get("type") == "input_json_delta":
                    block["_json"] += str(delta.get("partial_json") or "")
            elif kind == "message_delta":
                delta = payload.get("delta") or {}
                finish_reason = delta.get("stop_reason") or finish_reason
                extra = payload.get("usage") or {}
                usage = Usage(
                    usage.input_tokens + int(extra.get("input_tokens") or 0),
                    max(usage.output_tokens, int(extra.get("output_tokens") or 0)),
                )
            elif kind == "message_stop":
                break
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for _, block in sorted(blocks.items()):
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                arguments = block["_json"] or json.dumps(block.get("input") or {})
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or f"toolu_{len(calls)}"),
                        name=str(block.get("name") or ""),
                        arguments=arguments,
                    )
                )
        return ChatResponse(
            message=Message.assistant("".join(text_parts), calls),
            usage=usage,
            finish_reason=finish_reason,
            model=served_model,
        )

    def list_models(self) -> list[ModelInfo]:
        data = self.get_json("models?limit=1000")
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ProviderResponseError(f"{self.name}: unexpected /models response shape.")
        models = [
            ModelInfo(id=str(item["id"]), description=item.get("display_name"))
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]
        return sorted(models, key=lambda m: m.id)
