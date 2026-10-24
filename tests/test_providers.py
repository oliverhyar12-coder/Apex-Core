from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from apexcore.config import ProviderConfig
from apexcore.errors import (
    AuthenticationError,
    BadRequestError,
    ModelNotFoundError,
    NetworkError,
    ProviderError,
    ProviderResponseError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
)
from apexcore.providers.anthropic import AnthropicProvider, to_anthropic_messages
from apexcore.providers.base import Message, ToolCall, ToolSpec
from apexcore.providers.factory import create_provider
from apexcore.providers.openai_compat import OpenAICompatibleProvider, to_openai_messages

KEY = "sk-test-abcdefghijklmnopqrstuvwxyz"
TOOLS = [ToolSpec("read_file", "Read a file", {"type": "object", "properties": {}})]


def sse(*events: dict | str, named: bool = False) -> httpx.Response:
    lines = []
    for event in events:
        if isinstance(event, str):
            lines.append(f"data: {event}\n\n")
        elif named:
            lines.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
        else:
            lines.append(f"data: {json.dumps(event)}\n\n")
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content="".join(lines).encode()
    )


def openai(**kwargs) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(
        name="openai",
        base_url="https://api.test/v1",
        api_key=KEY,
        api_key_env="OPENAI_API_KEY",
        **kwargs,
    )
    provider._sleep = lambda seconds: None
    return provider


def anthropic(**kwargs) -> AnthropicProvider:
    provider = AnthropicProvider(
        name="anthropic",
        base_url="https://api.anthropic.test/v1",
        api_key=KEY,
        api_key_env="ANTHROPIC_API_KEY",
        **kwargs,
    )
    provider._sleep = lambda seconds: None
    return provider


# --- OpenAI-compatible -------------------------------------------------------


def test_openai_non_streaming_text(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )
    )
    result = openai().complete([Message.user("hi")], model="m")
    assert result.message.content == "Hello!"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 2)
    request = seen[0]
    assert request.url == "https://api.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert "stream" not in body and "temperature" not in body and "max_tokens" not in body


def test_openai_optional_fields_only_when_configured(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
    )
    openai(temperature=0.2, max_output_tokens=50).complete(
        [Message.user("hi")], model="m", tools=TOOLS
    )
    body = json.loads(seen[0].content)
    assert body["temperature"] == 0.2 and body["max_tokens"] == 50
    assert body["tools"][0]["function"]["name"] == "read_file"


def test_openai_no_auth_header_without_key(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
    )
    OpenAICompatibleProvider(
        name="ollama", base_url="http://localhost:11434/v1", api_key=None
    ).complete([Message.user("hi")], model="llama")
    assert "authorization" not in seen[0].headers


def test_openai_tool_calls_non_streaming(mock_http: Callable) -> None:
    mock_http(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": '{"path": "a"}'},
                                },
                                {
                                    "type": "function",
                                    "function": {"name": "list_files", "arguments": {"path": "."}},
                                },
                            ],
                        }
                    }
                ]
            },
        )
    )
    result = openai().complete([Message.user("hi")], model="m", tools=TOOLS)
    calls = result.message.tool_calls
    assert [c.name for c in calls] == ["read_file", "list_files"]
    assert calls[0].parse_arguments() == {"path": "a"}
    assert calls[1].id == "call_1" and calls[1].parse_arguments() == {"path": "."}


def test_openai_streaming_text_and_tool_calls(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: sse(
            {"model": "m", "choices": [{"delta": {"role": "assistant", "content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {"name": "read_file", "arguments": ""},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"pa'}}]}}
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"name": "read_file", "arguments": 'th": "x"}'},
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
            "[DONE]",
        )
    )
    deltas: list[str] = []
    result = openai().complete([Message.user("hi")], model="m", tools=TOOLS, on_text=deltas.append)
    assert deltas == ["Hel", "lo"]
    assert result.message.content == "Hello"
    assert result.message.tool_calls[0].name == "read_file"  # repeated name not duplicated
    assert result.message.tool_calls[0].parse_arguments() == {"path": "x"}
    assert result.finish_reason == "tool_calls"
    assert result.usage.output_tokens == 4
    body = json.loads(seen[0].content)
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}


def test_openai_streaming_can_be_disabled(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
    )
    deltas: list[str] = []
    openai(stream=False).complete([Message.user("hi")], model="m", on_text=deltas.append)
    assert "stream" not in json.loads(seen[0].content)
    assert deltas == ["x"]


def test_openai_message_conversion() -> None:
    call = ToolCall("c1", "read_file", '{"path": "a"}')
    wire = to_openai_messages(
        [
            Message.system("sys"),
            Message.user("u"),
            Message.assistant("", [call]),
            Message.tool_result(call, "data"),
        ]
    )
    assert wire[2]["content"] is None
    assert wire[2]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "a"}',
    }
    assert wire[3] == {"role": "tool", "tool_call_id": "c1", "content": "data"}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"error": {"message": "Incorrect API key"}}, AuthenticationError),
        (403, {"error": {"message": "forbidden"}}, AuthenticationError),
        (404, {"error": {"message": "The model `nope` does not exist"}}, ModelNotFoundError),
        (400, {"error": {"message": "Invalid model: nope"}}, ModelNotFoundError),
        (400, {"error": {"message": "tools are not supported"}}, BadRequestError),
        (429, {"error": {"message": "quota", "code": "insufficient_quota"}}, QuotaExceededError),
        (402, {"error": {"message": "pay up"}}, QuotaExceededError),
        (418, {"error": {"message": "teapot"}}, ProviderError),
    ],
)
def test_openai_error_mapping(mock_http: Callable, status: int, body: dict, error: type) -> None:
    seen = mock_http(lambda r: httpx.Response(status, json=body))
    with pytest.raises(error) as info:
        openai().complete([Message.user("hi")], model="m")
    assert len(seen) == 1  # not retryable
    assert KEY not in str(info.value) and KEY not in (info.value.hint or "")


def test_auth_error_hint_names_the_variable(mock_http: Callable) -> None:
    mock_http(lambda r: httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}))
    with pytest.raises(AuthenticationError) as info:
        openai().complete([Message.user("hi")], model="m")
    assert "OPENAI_API_KEY" in info.value.hint
    assert KEY not in info.value.message  # echoed key is redacted


def test_rate_limit_retries_then_succeeds(mock_http: Callable) -> None:
    responses = [
        httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "1"}),
        httpx.Response(503, text="unavailable"),
        httpx.Response(200, json={"choices": [{"message": {"content": "finally"}}]}),
    ]
    seen = mock_http(lambda r: responses.pop(0))
    provider = openai(max_retries=2)
    waits: list[float] = []
    provider._sleep = waits.append
    assert provider.complete([Message.user("hi")], model="m").message.content == "finally"
    assert len(seen) == 3 and waits[0] == 1.0


def test_rate_limit_gives_up(mock_http: Callable) -> None:
    seen = mock_http(lambda r: httpx.Response(429, json={"error": {"message": "slow"}}))
    with pytest.raises(RateLimitError):
        openai(max_retries=1).complete([Message.user("hi")], model="m")
    assert len(seen) == 2


def test_long_retry_after_is_not_waited(mock_http: Callable) -> None:
    seen = mock_http(lambda r: httpx.Response(429, json={}, headers={"retry-after": "3600"}))
    with pytest.raises(RateLimitError):
        openai(max_retries=3).complete([Message.user("hi")], model="m")
    assert len(seen) == 1


def test_server_errors_are_unavailable(mock_http: Callable) -> None:
    mock_http(lambda r: httpx.Response(500, text="<html>oops</html>"))
    with pytest.raises(ProviderUnavailableError):
        openai(max_retries=0).complete([Message.user("hi")], model="m")


def test_connect_error_and_timeout(mock_http: Callable) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    mock_http(refuse)
    with pytest.raises(NetworkError, match="could not connect"):
        openai(max_retries=0).complete([Message.user("hi")], model="m")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    mock_http(slow)
    with pytest.raises(NetworkError, match="timed out"):
        openai(max_retries=0).complete([Message.user("hi")], model="m")


def test_malformed_responses(mock_http: Callable) -> None:
    mock_http(lambda r: httpx.Response(200, text="not json"))
    with pytest.raises(ProviderResponseError):
        openai().complete([Message.user("hi")], model="m")
    mock_http(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(ProviderResponseError):
        openai().complete([Message.user("hi")], model="m")
    mock_http(lambda r: sse("{broken"))
    with pytest.raises(ProviderResponseError):
        openai().complete([Message.user("hi")], model="m", on_text=lambda t: None)


def test_stream_error_event(mock_http: Callable) -> None:
    mock_http(lambda r: sse({"error": {"message": "boom"}}))
    with pytest.raises(ProviderError, match="boom"):
        openai().complete([Message.user("hi")], model="m", on_text=lambda t: None)


def test_openai_list_models(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: httpx.Response(
            200, json={"data": [{"id": "b", "owned_by": "org"}, {"id": "a"}, {"bad": 1}]}
        )
    )
    models = openai().list_models()
    assert [m.id for m in models] == ["a", "b"]
    assert seen[0].method == "GET" and seen[0].url.path == "/v1/models"


def test_keyboard_interrupt_propagates(mock_http: Callable) -> None:
    def interrupt(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    mock_http(interrupt)
    with pytest.raises(KeyboardInterrupt):
        openai().complete([Message.user("hi")], model="m")


# --- Anthropic ---------------------------------------------------------------


def test_anthropic_message_conversion() -> None:
    c1 = ToolCall("toolu_1", "read_file", '{"path": "a"}')
    c2 = ToolCall("toolu_2", "list_files", "not json")
    system, wire = to_anthropic_messages(
        [
            Message.system("be nice"),
            Message.user("hi"),
            Message.assistant("let me look", [c1, c2]),
            Message.tool_result(c1, "contents"),
            Message.tool_result(c2, "", is_error=True),
            Message.user("thanks"),
        ]
    )
    assert system == "be nice"
    assert [m["role"] for m in wire] == ["user", "assistant", "user"]
    assert wire[1]["content"][1] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "read_file",
        "input": {"path": "a"},
    }
    assert wire[1]["content"][2]["input"] == {}
    results = wire[2]["content"]
    assert results[0] == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "contents"}
    assert results[1]["is_error"] is True
    assert results[2] == {"type": "text", "text": "thanks"}


def test_anthropic_non_streaming(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: httpx.Response(
            200,
            json={
                "model": "claude-x",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "Reading."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "a"},
                    },
                ],
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3},
            },
        )
    )
    result = anthropic().complete(
        [Message.system("sys"), Message.user("hi")], model="claude-x", tools=TOOLS
    )
    assert result.message.content == "Reading."
    assert result.message.tool_calls[0].parse_arguments() == {"path": "a"}
    assert (result.usage.input_tokens, result.usage.output_tokens) == (15, 3)
    request = seen[0]
    assert request.url == "https://api.anthropic.test/v1/messages"
    assert request.headers["x-api-key"] == KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["system"] == "sys" and body["max_tokens"] == 8192
    assert body["tools"][0]["input_schema"] == {"type": "object", "properties": {}}


def test_anthropic_streaming(mock_http: Callable) -> None:
    mock_http(
        lambda r: sse(
            {
                "type": "message_start",
                "message": {"model": "claude-x", "usage": {"input_tokens": 9, "output_tokens": 1}},
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {"type": "ping"},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hi "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "there"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_9",
                    "name": "read_file",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": ' "b"}'},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 12},
            },
            {"type": "message_stop"},
            named=True,
        )
    )
    deltas: list[str] = []
    result = anthropic().complete([Message.user("hi")], model="claude-x", on_text=deltas.append)
    assert deltas == ["Hi ", "there"]
    assert result.message.content == "Hi there"
    assert result.message.tool_calls[0].id == "toolu_9"
    assert result.message.tool_calls[0].parse_arguments() == {"path": "b"}
    assert (result.usage.input_tokens, result.usage.output_tokens) == (9, 12)
    assert result.finish_reason == "tool_use"


def test_anthropic_overloaded(mock_http: Callable) -> None:
    seen = mock_http(
        lambda r: httpx.Response(
            529,
            json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
        )
    )
    with pytest.raises(ProviderUnavailableError):
        anthropic(max_retries=1).complete([Message.user("hi")], model="claude-x")
    assert len(seen) == 2


def test_anthropic_stream_error(mock_http: Callable) -> None:
    mock_http(
        lambda r: sse(
            {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}, named=True
        )
    )
    with pytest.raises(ProviderUnavailableError):
        anthropic().complete([Message.user("hi")], model="claude-x", on_text=lambda t: None)


def test_anthropic_auth_error(mock_http: Callable) -> None:
    mock_http(
        lambda r: httpx.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )
    )
    with pytest.raises(AuthenticationError) as info:
        anthropic().complete([Message.user("hi")], model="claude-x")
    assert "ANTHROPIC_API_KEY" in info.value.hint


def test_anthropic_list_models(mock_http: Callable) -> None:
    mock_http(
        lambda r: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-b", "display_name": "B"},
                    {"id": "claude-a", "display_name": "A"},
                ]
            },
        )
    )
    assert [m.id for m in anthropic().list_models()] == ["claude-a", "claude-b"]


# --- Factory -----------------------------------------------------------------


def test_factory_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = ProviderConfig(base_url="https://api.test/v1", api_key_env="OPENAI_API_KEY")
    with pytest.raises(AuthenticationError, match="No API key"):
        create_provider("openai", cfg)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    provider = create_provider("openai", cfg)
    assert isinstance(provider, OpenAICompatibleProvider)
    from apexcore.redaction import GLOBAL_REDACTOR

    assert KEY not in GLOBAL_REDACTOR.redact(f"x {KEY} y")


def test_factory_picks_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    cfg = ProviderConfig(
        adapter="anthropic",
        base_url="https://api.anthropic.test/v1",
        api_key_env="ANTHROPIC_API_KEY",
    )
    assert isinstance(create_provider("anthropic", cfg), AnthropicProvider)
    local = ProviderConfig(base_url="http://localhost:11434/v1")
    assert isinstance(create_provider("ollama", local), OpenAICompatibleProvider)


def test_cancel_replaces_the_http_client(mock_http: Callable) -> None:
    mock_http(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    provider = openai()
    old_client = provider._client
    provider.cancel()
    assert old_client.is_closed and provider._client is not old_client
    assert provider.complete([Message.user("hi")], model="m").message.content == "ok"
