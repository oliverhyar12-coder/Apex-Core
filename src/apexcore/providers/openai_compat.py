"""Adapter for the OpenAI Chat Completions wire format.

Many services implement this format (OpenAI, DeepSeek, Mistral, Groq,
OpenRouter, Ollama, LM Studio, Google's compatibility endpoint...), but they
differ in details. The adapter therefore only sends optional fields that the
user configured, tolerates missing ids and usage, and lets streaming be
switched off per provider (``stream = false``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from apexcore.errors import ProviderError, ProviderResponseError
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


def to_openai_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            out.append({"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content})
        elif msg.role == "assistant" and msg.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments or "{}"},
                        }
                        for call in msg.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": msg.role, "content": msg.content})
    return out


def to_openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def _usage(data: Any) -> Usage | None:
    if not isinstance(data, dict):
        return None
    return Usage(
        input_tokens=int(data.get("prompt_tokens") or 0),
        output_tokens=int(data.get("completion_tokens") or 0),
    )


class OpenAICompatibleProvider(HTTPProviderMixin, Provider):
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
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
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
        self.max_output_tokens = max_output_tokens

    def _payload(
        self,
        messages: Sequence[Message],
        model: str,
        tools: Sequence[ToolSpec] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": to_openai_messages(messages)}
        if tools and self.supports_tools:
            payload["tools"] = to_openai_tools(tools)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_output_tokens is not None:
            payload["max_tokens"] = self.max_output_tokens
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        on_text: TextCallback | None = None,
    ) -> ChatResponse:
        stream = self.supports_streaming and on_text is not None
        payload = self._payload(messages, model, tools, stream)
        if not stream:
            with self.request("POST", "chat/completions", json_body=payload) as response:
                data = self.parse_json(response)
            result = self._parse_completion(data, model)
            if on_text and result.message.content:
                on_text(result.message.content)
            return result
        with self.request("POST", "chat/completions", json_body=payload, stream=True) as response:
            return self._parse_stream(response, model, on_text)

    def _parse_completion(self, data: Any, model: str) -> ChatResponse:
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"{self.name}: {self._clean(str(data['error']))[:300]}")
        try:
            choice = data["choices"][0]
            msg = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                f"{self.name}: unexpected response shape (no choices)."
            ) from exc
        calls = []
        for index, raw in enumerate(msg.get("tool_calls") or []):
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, dict):  # some servers send an object
                arguments = json.dumps(arguments)
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments or "{}",
                )
            )
        return ChatResponse(
            message=Message.assistant(str(msg.get("content") or ""), calls),
            usage=_usage(data.get("usage")),
            finish_reason=choice.get("finish_reason"),
            model=data.get("model") or model,
        )

    def _parse_stream(
        self, response: Any, model: str, on_text: TextCallback | None
    ) -> ChatResponse:
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        finish_reason: str | None = None
        served_model = model
        for _event, data in iter_sse(response, self):
            if data.strip() == "[DONE]":
                break
            chunk = load_event_json(data, self)
            if chunk.get("error"):
                raise ProviderError(f"{self.name}: {self._clean(str(chunk['error']))[:300]}")
            served_model = chunk.get("model") or served_model
            if chunk.get("usage"):
                usage = _usage(chunk["usage"])
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    if on_text:
                        on_text(content)
                for position, raw in enumerate(delta.get("tool_calls") or []):
                    index = raw.get("index", position)
                    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if raw.get("id"):
                        slot["id"] = raw["id"]
                    function = raw.get("function") or {}
                    name = function.get("name")
                    # Most servers send the name once; some repeat it in every chunk.
                    if name and name != slot["name"]:
                        slot["name"] += name
                    if function.get("arguments"):
                        slot["arguments"] += function["arguments"]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
        tool_calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
            for index, slot in sorted(calls.items())
        ]
        return ChatResponse(
            message=Message.assistant("".join(text_parts), tool_calls),
            usage=usage,
            finish_reason=finish_reason,
            model=served_model,
        )

    def list_models(self) -> list[ModelInfo]:
        data = self.get_json("models")
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ProviderResponseError(f"{self.name}: unexpected /models response shape.")
        models = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                owner = item.get("owned_by")
                models.append(
                    ModelInfo(id=str(item["id"]), description=str(owner) if owner else None)
                )
        return sorted(models, key=lambda m: m.id)
