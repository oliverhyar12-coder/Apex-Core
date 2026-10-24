"""The bounded agent loop.

One call to :meth:`Agent.run` handles one user turn:

1. send the conversation (and tool specs) to the model;
2. if the model answered with text only, the turn is complete;
3. otherwise validate each tool call, check permissions, ask the user when
   required, execute, and append the (redacted, truncated) result;
4. repeat, at most ``max_steps`` model calls.

``Ctrl+C`` at any point ends the turn with status ``cancelled``. The
conversation is left in a valid state: any tool call that did not run gets a
"cancelled" result, so the next request is accepted by the provider.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from apexcore.errors import ApexError, ToolError
from apexcore.permissions import (
    ApprovalRequest,
    Approver,
    Decision,
    PermissionPolicy,
)
from apexcore.platform_utils import call_interruptibly
from apexcore.providers.base import ChatResponse, Message, Provider, ToolCall, ToolSpec, Usage
from apexcore.tools.base import Risk, Tool, ToolContext, ToolResult, truncate
from apexcore.tools.registry import known_tool_names

TurnStatus = Literal["completed", "max_steps", "cancelled", "failed"]
ActionStatus = Literal["ok", "error", "denied", "cancelled"]


@dataclass
class ActionRecord:
    tool: str
    summary: str
    status: ActionStatus
    detail: str = ""
    # Seconds the tool ran for (None if it never ran).
    duration: float | None = None
    # Short text for the user (e.g. a plan checklist or a command's last lines).
    display: str = ""


@dataclass
class TurnResult:
    status: TurnStatus
    text: str = ""
    actions: list[ActionRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    usage_reported: bool = False
    steps: int = 0
    error: ApexError | None = None


class AgentUI(Protocol):
    def thinking(self) -> None: ...
    def assistant_text(self, text: str) -> None: ...
    def assistant_done(self, message: Message) -> None: ...
    def tool_started(self, call: ToolCall, summary: str) -> None: ...
    def tool_running(self, call: ToolCall, summary: str) -> None: ...
    def tool_finished(self, record: ActionRecord) -> None: ...
    def notice(self, text: str) -> None: ...


class NullUI:
    def thinking(self) -> None:
        pass

    def assistant_text(self, text: str) -> None:
        pass

    def assistant_done(self, message: Message) -> None:
        pass

    def tool_started(self, call: ToolCall, summary: str) -> None:
        pass

    def tool_running(self, call: ToolCall, summary: str) -> None:
        pass

    def tool_finished(self, record: ActionRecord) -> None:
        pass

    def notice(self, text: str) -> None:
        pass


def _short_args(arguments: str, limit: int = 80) -> str:
    text = " ".join(arguments.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class Agent:
    def __init__(
        self,
        *,
        provider: Provider,
        model: str,
        tools: Sequence[Tool],
        ctx: ToolContext | None,
        policy: PermissionPolicy,
        approver: Approver,
        ui: AgentUI | None = None,
        max_steps: int = 12,
        on_message: Callable[[Message], None] | None = None,
        on_action: Callable[[ActionRecord], None] | None = None,
    ) -> None:
        if tools and ctx is None:
            raise ValueError("a ToolContext is required when tools are enabled")
        self.provider = provider
        self.model = model
        self.tools = {tool.name: tool for tool in tools} if provider.supports_tools else {}
        self.ctx = ctx
        self.policy = policy
        self.approver = approver
        self.ui = ui or NullUI()
        self.max_steps = max_steps
        self.on_message = on_message
        self.on_action = on_action

    # --- Helpers ---------------------------------------------------------

    def _append(self, conversation: list[Message], message: Message) -> None:
        conversation.append(message)
        if self.on_message:
            self.on_message(message)

    def _record(self, result: TurnResult, record: ActionRecord) -> None:
        result.actions.append(record)
        self.ui.tool_finished(record)
        if self.on_action:
            self.on_action(record)

    def _limit(self, text: str) -> str:
        limit = self.ctx.tool_settings.max_output_chars if self.ctx else 20_000
        return truncate(text, limit, what="tool output")

    # --- Main loop -------------------------------------------------------

    def run(self, conversation: list[Message]) -> TurnResult:
        """Run one user turn. ``conversation`` is extended in place."""
        result = TurnResult(status="completed")
        specs = [tool.spec() for tool in self.tools.values()]
        pending: list[ToolCall] = []
        streamed: list[str] = []

        def on_text(delta: str) -> None:
            streamed.append(delta)
            self.ui.assistant_text(delta)

        try:
            for step in range(1, self.max_steps + 1):
                result.steps = step
                streamed.clear()
                self.ui.thinking()
                response = self._complete(list(conversation), specs, on_text)
                if response.usage is not None:
                    result.usage = result.usage + response.usage
                    result.usage_reported = True
                message = response.message
                streamed.clear()
                self._append(conversation, message)
                self.ui.assistant_done(message)
                if message.content:
                    result.text = message.content
                if not message.tool_calls:
                    return result
                pending = list(message.tool_calls)
                while pending:
                    call = pending[0]
                    content, ok = self._handle_call(call, result)
                    self._append(conversation, Message.tool_result(call, content, is_error=not ok))
                    pending.pop(0)
            result.status = "max_steps"
            self.ui.notice(
                f"Stopped after {self.max_steps} steps (agent.max_steps). "
                "Ask me to continue if more work is needed."
            )
            return result
        except KeyboardInterrupt:
            result.status = "cancelled"
            if streamed:
                partial = "".join(streamed) + "\n[interrupted]"
                self._append(conversation, Message.assistant(partial))
                result.text = partial
            for call in pending:
                self._append(
                    conversation, Message.tool_result(call, "Cancelled by the user.", is_error=True)
                )
                self._record(result, ActionRecord(call.name, call.name, "cancelled"))
            return result
        except ApexError as exc:
            result.status = "failed"
            result.error = exc
            for call in pending:
                self._append(
                    conversation,
                    Message.tool_result(call, "Not run: the turn failed.", is_error=True),
                )
            return result

    def _complete(
        self, messages: list[Message], specs: list[ToolSpec], on_text: Callable[[str], None]
    ) -> ChatResponse:
        """Call the model in a worker thread so Ctrl+C works even while a
        socket read is blocked (Windows cannot interrupt one)."""
        cancelled = threading.Event()

        def guarded_text(delta: str) -> None:
            if cancelled.is_set():  # stop a cancelled stream from printing more
                raise KeyboardInterrupt
            on_text(delta)

        def cancel() -> None:
            cancelled.set()
            self.provider.cancel()

        return call_interruptibly(
            lambda: self.provider.complete(
                messages, model=self.model, tools=specs or None, on_text=guarded_text
            ),
            cancel,
        )

    def _handle_call(self, call: ToolCall, result: TurnResult) -> tuple[str, bool]:
        """Validate, authorize, and run one tool call. Returns (content, ok)."""
        tool = self.tools.get(call.name)
        if tool is None:
            available = ", ".join(sorted(self.tools)) or "none"
            if call.name in known_tool_names():
                self.ui.tool_started(call, f"{call.name} (not enabled)")
                self._record(result, ActionRecord(call.name, "tool not enabled", "denied"))
                return (
                    f"Not allowed: {call.name} is not enabled in this session (read-only "
                    f"mode, disabled by settings, or no terminal to approve it). "
                    f"Available tools: {available}."
                ), False
            self.ui.tool_started(call, f"{call.name} (unknown tool)")
            self._record(result, ActionRecord(call.name, "unknown tool", "error"))
            return f"Error: unknown tool {call.name!r}. Available tools: {available}.", False
        try:
            raw_args = call.parse_arguments()
        except (ValueError, json.JSONDecodeError) as exc:
            self.ui.tool_started(call, f"{call.name} {_short_args(call.arguments)}")
            self._record(result, ActionRecord(call.name, "invalid arguments", "error", str(exc)))
            return (
                f"Error: arguments are not a valid JSON object ({exc}). Retry with valid JSON.",
                False,
            )
        assert self.ctx is not None
        try:
            args = tool.parse_args(raw_args)
            action = tool.prepare(args, self.ctx)
        except (ToolError, OSError) as exc:
            message = exc.message if isinstance(exc, ToolError) else f"{type(exc).__name__}: {exc}"
            message = self.ctx.redactor.redact(message)
            self.ui.tool_started(call, f"{call.name} {_short_args(call.arguments)}")
            self._record(result, ActionRecord(call.name, call.name, "error", message))
            return f"Error: {message}", False

        self.ui.tool_started(call, action.summary)
        decision, reason = self.policy.decide(tool, action)
        if decision is Decision.DENY:
            self._record(result, ActionRecord(tool.name, action.summary, "denied", reason))
            return f"Not allowed: {reason}. Do not retry this action.", False
        if decision is Decision.ASK:
            response = self.approver.confirm(
                ApprovalRequest(
                    tool=tool.name,
                    risk=tool.risk,
                    summary=action.summary,
                    details=action.details,
                    warnings=action.warnings,
                    can_remember=action.auto_approvable,
                    can_always=tool.risk is Risk.EXECUTE and action.auto_approvable,
                )
            )
            if not response.approved:
                self._record(result, ActionRecord(tool.name, action.summary, "denied", "by user"))
                message = "The user denied this action."
                if response.feedback:
                    message += f" Their feedback: {response.feedback}"
                return message + " Do not retry it unchanged.", False
            self.policy.remember(tool, action, response.scope)
        self.ui.tool_running(call, action.summary)
        started = time.monotonic()
        try:
            outcome: ToolResult = action.run()
        except ToolError as exc:
            elapsed = time.monotonic() - started
            record = ActionRecord(tool.name, action.summary, "error", exc.message, elapsed)
            self._record(result, record)
            return f"Error: {exc.message}", False
        except OSError as exc:
            elapsed = time.monotonic() - started
            detail = self.ctx.redactor.redact(f"{type(exc).__name__}: {exc}")
            self._record(result, ActionRecord(tool.name, action.summary, "error", detail, elapsed))
            return f"Error: {detail}", False
        status: ActionStatus = "ok" if outcome.ok else "error"
        first_line = outcome.content.strip().splitlines()[0] if outcome.content.strip() else ""
        record = ActionRecord(
            tool.name,
            action.summary,
            status,
            first_line[:200],
            duration=time.monotonic() - started,
            display=self.ctx.redactor.redact(outcome.display),
        )
        self._record(result, record)
        return self._limit(self.ctx.redactor.redact(outcome.content)), outcome.ok
