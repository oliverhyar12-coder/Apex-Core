"""Test doubles: a scripted provider and a local OpenAI-compatible HTTP server."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

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


def text_response(text: str, usage: Usage | None = None) -> ChatResponse:
    return ChatResponse(message=Message.assistant(text), usage=usage or Usage(10, 5))


def tool_response(*calls: tuple[str, dict[str, Any]], text: str = "") -> ChatResponse:
    tool_calls = [
        ToolCall(id=f"call_{i}", name=name, arguments=json.dumps(args))
        for i, (name, args) in enumerate(calls)
    ]
    return ChatResponse(message=Message.assistant(text, tool_calls), usage=Usage(20, 10))


class ScriptedProvider(Provider):
    """Returns pre-programmed responses; items may also be exceptions or callables."""

    name = "fake"

    def __init__(self, responses: Sequence[ChatResponse | BaseException | Callable[[], Any]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        on_text: TextCallback | None = None,
    ) -> ChatResponse:
        self.calls.append({"messages": list(messages), "model": model, "tools": tools})
        if not self.responses:
            raise AssertionError("ScriptedProvider ran out of responses")
        item = self.responses.pop(0)
        if callable(item) and not isinstance(item, ChatResponse):
            item = item()
        if isinstance(item, BaseException):
            raise item
        if on_text and item.message.content:
            on_text(item.message.content)
        return item

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo("fake-model")]

    def close(self) -> None:
        self.closed = True


class FakeOpenAIServer:
    """A tiny OpenAI-compatible server on 127.0.0.1 for end-to-end tests.

    Script items: ``{"content": str}``, ``{"tool_calls": [(name, args), ...]}``,
    ``{"status": 401, "error": "..."}``, or ``{"hang": True}`` (never answers
    until the server stops; used for Ctrl+C tests).
    """

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = list(script or [])
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.request_event = threading.Event()
        self._stop = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def _send_json(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                server.headers.append(dict(self.headers))
                if self.path.endswith("/models"):
                    self._send_json(200, {"data": [{"id": "fake-model", "owned_by": "test"}]})
                else:
                    self._send_json(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append(body)
                server.headers.append(dict(self.headers))
                server.request_event.set()
                item = server.script.pop(0) if server.script else {"content": "ok"}
                if item.get("hang"):
                    server._stop.wait(30)
                    return
                if "status" in item:
                    self._send_json(item["status"], {"error": {"message": item.get("error", "")}})
                    return
                if body.get("stream"):
                    self._stream(item)
                else:
                    self._send_json(200, server.completion(item))

            def _stream(self, item: dict[str, Any]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for chunk in server.chunks(item):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    @staticmethod
    def _tool_calls(item: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(item.get("tool_calls", []))
        ]

    def completion(self, item: dict[str, Any]) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": item.get("content", "")}
        if item.get("tool_calls"):
            message["tool_calls"] = self._tool_calls(item)
        return {
            "model": "fake-model",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }

    def chunks(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        content = item.get("content", "")
        for i in range(0, len(content), 4):
            out.append({"choices": [{"index": 0, "delta": {"content": content[i : i + 4]}}]})
        for index, call in enumerate(self._tool_calls(item)):
            out.append(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": call["id"],
                                        "type": "function",
                                        "function": {
                                            "name": call["function"]["name"],
                                            "arguments": "",
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
            args = call["function"]["arguments"]
            for j in range(0, len(args), 5):
                out.append(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {"index": index, "function": {"arguments": args[j : j + 5]}}
                                    ]
                                },
                            }
                        ]
                    }
                )
        out.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        out.append({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}})
        return out

    def __enter__(self) -> FakeOpenAIServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        time.sleep(0.01)
