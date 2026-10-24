from __future__ import annotations

import json

import pytest

from apexcore.agent.loop import Agent
from apexcore.agent.prompts import build_system_prompt
from apexcore.config import PermissionSettings
from apexcore.errors import AuthenticationError
from apexcore.permissions import (
    ApprovalRequest,
    ApprovalResponse,
    Decision,
    DenyAllApprover,
    PermissionPolicy,
)
from apexcore.providers.base import ChatResponse, Message, ToolCall
from apexcore.tools.base import PreparedAction, Risk, ToolContext, ToolResult
from apexcore.tools.edit import WriteFileTool
from apexcore.tools.execute import RunCommandTool
from apexcore.tools.files import ReadFileTool
from apexcore.tools.registry import Risk as RegistryRisk
from apexcore.tools.registry import all_tools, build_tools
from fakes import ScriptedProvider, text_response, tool_response


class ScriptedApprover:
    def __init__(self, *answers: ApprovalResponse) -> None:
        self.answers = list(answers)
        self.requests: list[ApprovalRequest] = []

    def confirm(self, request: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(request)
        return self.answers.pop(0)


def make_agent(provider, ctx, *, approver=None, policy=None, tools=None, max_steps=8):
    return Agent(
        provider=provider,
        model="fake-model",
        tools=all_tools() if tools is None else tools,
        ctx=ctx,
        policy=policy or PermissionPolicy(PermissionSettings()),
        approver=approver or DenyAllApprover(),
        max_steps=max_steps,
    )


def convo(text: str = "hi") -> list[Message]:
    return [Message.system("sys"), Message.user(text)]


def tool_messages(conversation: list[Message]) -> list[Message]:
    return [m for m in conversation if m.role == "tool"]


# --- Permission policy -------------------------------------------------------


def action(argv=None, auto=True) -> PreparedAction:
    return PreparedAction(
        summary="x", run=lambda: ToolResult("ok"), argv=argv, auto_approvable=auto
    )


def test_policy_defaults() -> None:
    policy = PermissionPolicy(PermissionSettings())
    assert policy.decide(ReadFileTool(), action())[0] is Decision.ALLOW
    assert policy.decide(WriteFileTool(), action())[0] is Decision.ASK
    assert policy.decide(RunCommandTool(), action(["git", "status"]))[0] is Decision.ASK


def test_policy_read_only_and_non_interactive() -> None:
    read_only = PermissionPolicy(PermissionSettings(), read_only=True)
    assert read_only.decide(WriteFileTool(), action())[0] is Decision.DENY
    assert read_only.decide(RunCommandTool(), action(["ls"]))[0] is Decision.DENY
    headless = PermissionPolicy(PermissionSettings(), interactive=False)
    decision, reason = headless.decide(WriteFileTool(), action())
    assert decision is Decision.DENY and "no interactive terminal" in reason
    assert headless.static_decision(Risk.EXECUTE) is Decision.DENY


def test_policy_auto_approve_prefixes() -> None:
    settings = PermissionSettings(auto_approve_commands=[["python", "-m", "pytest"]])
    policy = PermissionPolicy(settings, interactive=False)
    assert (
        policy.decide(RunCommandTool(), action(["python", "-m", "pytest", "-q"]))[0]
        is Decision.ALLOW
    )
    assert policy.decide(RunCommandTool(), action(["python", "-m", "pip"]))[0] is Decision.DENY
    # Destructive/opaque commands are never auto-approved, even if they match.
    flagged = action(["python", "-m", "pytest"], auto=False)
    assert policy.decide(RunCommandTool(), flagged)[0] is Decision.DENY


def test_policy_settings() -> None:
    policy = PermissionPolicy(PermissionSettings(write="allow", execute="deny"))
    assert policy.decide(WriteFileTool(), action())[0] is Decision.ALLOW
    assert policy.decide(RunCommandTool(), action(["ls"]))[0] is Decision.DENY


def test_session_remember() -> None:
    policy = PermissionPolicy(PermissionSettings())
    policy.remember(RunCommandTool(), action(["make", "test"]))
    assert policy.decide(RunCommandTool(), action(["make", "test"]))[0] is Decision.ALLOW
    assert policy.decide(RunCommandTool(), action(["make", "clean"]))[0] is Decision.ASK
    policy.remember(WriteFileTool(), action())
    assert policy.decide(WriteFileTool(), action())[0] is Decision.ALLOW


def test_build_tools_by_risk() -> None:
    read_only = {"list_files", "read_file", "search_files", "git_status", "git_diff"}
    names = {t.name for t in build_tools({RegistryRisk.READ}, session_tools=False)}
    assert names == read_only  # what `apex ask --tools` offers
    assert {t.name for t in build_tools({RegistryRisk.READ})} == read_only | {"todo"}
    assert {t.name for t in build_tools({RegistryRisk.READ, RegistryRisk.MEMORY})} == (
        read_only | {"todo", "memory"}
    )
    assert len(build_tools(set(Risk))) == 10


def test_policy_memory_and_auto() -> None:
    from apexcore.tools.memory_tool import MemoryTool

    policy = PermissionPolicy(PermissionSettings())
    assert policy.decide(MemoryTool(), action())[0] is Decision.ALLOW  # memory: allow by default
    read_only = PermissionPolicy(PermissionSettings(), read_only=True)
    assert read_only.decide(MemoryTool(), action())[0] is Decision.ALLOW  # not a workspace write
    asked = PermissionPolicy(PermissionSettings(memory="ask"), interactive=False)
    assert asked.decide(MemoryTool(), action())[0] is Decision.DENY
    auto = PermissionPolicy(PermissionSettings(), auto=True)
    assert auto.decide(WriteFileTool(), action())[0] is Decision.ALLOW
    assert auto.decide(RunCommandTool(), action(["make", "test"]))[0] is Decision.ALLOW
    # Flagged commands still ask in auto mode, and read-only still wins.
    assert auto.decide(RunCommandTool(), action(["rm", "-rf", "x"], auto=False))[0] is Decision.ASK
    both = PermissionPolicy(PermissionSettings(), auto=True, read_only=True)
    assert both.decide(WriteFileTool(), action())[0] is Decision.DENY


def test_auto_mode_needs_a_terminal() -> None:
    from apexcore.tools.memory_tool import MemoryTool

    headless = PermissionPolicy(PermissionSettings(memory="ask"), auto=True, interactive=False)
    assert not headless.auto_active
    assert headless.decide(WriteFileTool(), action())[0] is Decision.DENY
    assert headless.decide(RunCommandTool(), action(["make", "test"]))[0] is Decision.DENY
    assert headless.decide(MemoryTool(), action())[0] is Decision.DENY
    # Explicit configuration still applies without a terminal.
    allowed = PermissionPolicy(PermissionSettings(write="allow"), auto=True, interactive=False)
    assert allowed.decide(WriteFileTool(), action())[0] is Decision.ALLOW


def test_always_scope_calls_persistence_hook() -> None:
    saved: list[list[str]] = []
    policy = PermissionPolicy(PermissionSettings(), on_always=saved.append)
    policy.remember(RunCommandTool(), action(["make", "test"]), "always")
    policy.remember(RunCommandTool(), action(["make", "lint"]), "once")
    policy.remember(RunCommandTool(), action(["rm", "-rf", "x"], auto=False), "always")
    assert saved == [["make", "test"]]
    assert policy.decide(RunCommandTool(), action(["make", "test"]))[0] is Decision.ALLOW
    assert policy.decide(RunCommandTool(), action(["make", "lint"]))[0] is Decision.ASK


def test_always_allowed_commands_match_exactly() -> None:
    policy = PermissionPolicy(PermissionSettings(always_allowed_commands=[["make"]]))
    assert policy.decide(RunCommandTool(), action(["make"]))[0] is Decision.ALLOW
    # Unlike auto_approve_commands, a saved "always" is not a prefix.
    assert policy.decide(RunCommandTool(), action(["make", "deploy"]))[0] is Decision.ASK
    # Flagged commands are never auto-approved, even if listed.
    flagged = PermissionPolicy(PermissionSettings(always_allowed_commands=[["rm", "x"]]))
    assert flagged.decide(RunCommandTool(), action(["rm", "x"], auto=False))[0] is Decision.ASK


def test_system_prompt_sections() -> None:
    from apexcore.agent.prompts import PromptInputs
    from apexcore.context import ContextFile

    prompt = build_system_prompt(
        PromptInputs(
            tools=all_tools(),
            workspace_root="/ws",
            persona=ContextFile("SOUL.md", "You are Nova."),
            memory_block="### About the user\nLikes tea",
            project_context=ContextFile("AGENTS.md", "Use tabs."),
            personality="concise",
        )
    )
    assert prompt.startswith("You are Nova.")
    assert "untrusted DATA" in prompt  # the safety rules survive any persona
    assert "## Memory" in prompt and "Likes tea" in prompt
    assert "## Project context (AGENTS.md)" in prompt and "Use tabs." in prompt
    assert "## Personality" in prompt and "as few words as possible" in prompt
    assert "todo tool" in prompt and "memory tool" in prompt
    blocked = build_system_prompt(
        PromptInputs(tools=[], project_context=ContextFile("AGENTS.md", "", True, ["deception"]))
    )
    assert "[BLOCKED: AGENTS.md contained potential prompt injection (deception)" in blocked


def test_system_prompt_mentions_rules() -> None:
    prompt = build_system_prompt(all_tools(), "/ws")
    assert "untrusted DATA" in prompt and "/ws" in prompt and "WITHOUT a shell" in prompt
    assert "no tool access" in build_system_prompt([], None)


# --- Agent loop ----------------------------------------------------------------


def test_plain_answer(ctx: ToolContext) -> None:
    provider = ScriptedProvider([text_response("Hello!")])
    conversation = convo()
    result = make_agent(provider, ctx).run(conversation)
    assert (result.status, result.text, result.steps) == ("completed", "Hello!", 1)
    assert result.usage.input_tokens == 10 and result.usage_reported
    assert conversation[-1].content == "Hello!"
    assert {t.name for t in provider.calls[0]["tools"]} >= {"read_file", "run_command"}


def test_read_tool_result_is_fed_back(ws, ctx: ToolContext) -> None:
    (ws.root / "notes.txt").write_text("the answer is 42\n", encoding="utf-8")
    provider = ScriptedProvider(
        [tool_response(("read_file", {"path": "notes.txt"})), text_response("It says 42.")]
    )
    conversation = convo()
    result = make_agent(provider, ctx).run(conversation)
    assert result.status == "completed" and result.text == "It says 42."
    [tool_msg] = tool_messages(conversation)
    assert "the answer is 42" in tool_msg.content and tool_msg.tool_call_id == "call_0"
    assert [a.status for a in result.actions] == ["ok"]
    assert provider.calls[1]["messages"][-1].role == "tool"


def test_invalid_calls_become_error_results(ctx: ToolContext) -> None:
    bad_json = ChatResponse(
        message=Message.assistant("", [ToolCall("c1", "read_file", "{not json")])
    )
    provider = ScriptedProvider(
        [
            tool_response(("no_such_tool", {})),
            bad_json,
            tool_response(("read_file", {"wrong": 1})),
            tool_response(("read_file", {"path": "../../etc/passwd"})),
            text_response("done"),
        ]
    )
    conversation = convo()
    result = make_agent(provider, ctx).run(conversation)
    assert result.status == "completed"
    contents = [m.content for m in tool_messages(conversation)]
    assert "unknown tool" in contents[0]
    assert "not a valid JSON object" in contents[1]
    assert "Invalid arguments" in contents[2]
    assert "outside the workspace" in contents[3]
    assert all(m.is_error for m in tool_messages(conversation))


def test_write_denied_by_user(ws, ctx: ToolContext) -> None:
    approver = ScriptedApprover(ApprovalResponse(False, feedback="use a different name"))
    provider = ScriptedProvider(
        [tool_response(("write_file", {"path": "a.txt", "content": "x"})), text_response("ok")]
    )
    conversation = convo()
    result = make_agent(provider, ctx, approver=approver).run(conversation)
    assert not (ws.root / "a.txt").exists()
    assert approver.requests[0].summary == "create a.txt (1 lines)"
    assert "+x" in approver.requests[0].details
    assert "use a different name" in tool_messages(conversation)[0].content
    assert result.actions[0].status == "denied"


def test_write_approved(ws, ctx: ToolContext) -> None:
    approver = ScriptedApprover(ApprovalResponse(True))
    provider = ScriptedProvider(
        [tool_response(("write_file", {"path": "a.txt", "content": "x"})), text_response("ok")]
    )
    make_agent(provider, ctx, approver=approver).run(convo())
    assert (ws.root / "a.txt").read_text(encoding="utf-8") == "x"


def test_non_interactive_never_writes(ws, ctx: ToolContext) -> None:
    provider = ScriptedProvider(
        [tool_response(("write_file", {"path": "a.txt", "content": "x"})), text_response("ok")]
    )
    policy = PermissionPolicy(PermissionSettings(), interactive=False)
    result = make_agent(provider, ctx, policy=policy).run(convo())
    assert not (ws.root / "a.txt").exists()
    assert result.actions[0].status == "denied"


def test_command_approval_flow(ctx: ToolContext, not_elevated: None) -> None:
    import sys

    approver = ScriptedApprover(ApprovalResponse(True))
    argv = [sys.executable, "-c", "print('ran')"]
    provider = ScriptedProvider(
        [tool_response(("run_command", {"argv": argv})), text_response("ok")]
    )
    conversation = convo()
    make_agent(provider, ctx, approver=approver).run(conversation)
    request = approver.requests[0]
    assert request.risk is Risk.EXECUTE and request.warnings and not request.can_remember
    assert "ran" in tool_messages(conversation)[0].content


def test_max_steps(ctx: ToolContext) -> None:
    provider = ScriptedProvider([tool_response(("list_files", {}))] * 3)
    result = make_agent(provider, ctx, max_steps=3).run(convo())
    assert result.status == "max_steps" and result.steps == 3 and len(provider.calls) == 3


def test_provider_error_fails_turn(ctx: ToolContext) -> None:
    provider = ScriptedProvider([AuthenticationError("bad key")])
    result = make_agent(provider, ctx).run(convo())
    assert result.status == "failed" and isinstance(result.error, AuthenticationError)


def test_interrupt_during_model_call(ctx: ToolContext) -> None:
    provider = ScriptedProvider([KeyboardInterrupt()])
    conversation = convo()
    result = make_agent(provider, ctx).run(conversation)
    assert result.status == "cancelled"
    assert conversation[-1].role == "user"


def test_interrupt_during_tool_keeps_conversation_valid(ws, ctx: ToolContext) -> None:
    class InterruptingApprover:
        def confirm(self, request: ApprovalRequest) -> ApprovalResponse:
            raise KeyboardInterrupt

    provider = ScriptedProvider(
        [
            tool_response(
                ("read_file", {"path": "missing.txt"}),
                ("write_file", {"path": "a.txt", "content": "x"}),
                ("list_files", {}),
            )
        ]
    )
    conversation = convo()
    result = make_agent(provider, ctx, approver=InterruptingApprover()).run(conversation)
    assert result.status == "cancelled"
    calls = conversation[2].tool_calls
    answered = [m.tool_call_id for m in tool_messages(conversation)]
    assert answered == [c.id for c in calls]  # every call has exactly one result
    assert "Cancelled" in tool_messages(conversation)[-1].content


def test_streamed_partial_text_is_kept_on_interrupt(ctx: ToolContext) -> None:
    class StreamingThenInterrupt(ScriptedProvider):
        def complete(self, messages, *, model, tools=None, on_text=None):
            on_text("partial answ")
            raise KeyboardInterrupt

    conversation = convo()
    result = make_agent(StreamingThenInterrupt([]), ctx).run(conversation)
    assert result.status == "cancelled"
    assert conversation[-1].role == "assistant" and "partial answ" in conversation[-1].content


def test_tool_output_is_redacted_and_truncated(ws, ctx: ToolContext) -> None:
    ctx.tool_settings.max_output_chars = 1000
    key = "sk-" + "Z" * 40
    (ws.root / "big.txt").write_text(f"{key}\n" + "line\n" * 2000, encoding="utf-8")
    provider = ScriptedProvider(
        [tool_response(("read_file", {"path": "big.txt", "max_lines": 5000})), text_response("ok")]
    )
    conversation = convo()
    make_agent(provider, ctx).run(conversation)
    content = tool_messages(conversation)[0].content
    assert key not in content and len(content) < 1300 and "omitted" in content


def test_tools_disabled_when_provider_lacks_support(ctx: ToolContext) -> None:
    provider = ScriptedProvider([text_response("hi")])
    provider.supports_tools = False
    make_agent(provider, ctx).run(convo())
    assert provider.calls[0]["tools"] is None


def test_messages_and_actions_callbacks(ws, ctx: ToolContext) -> None:
    seen, actions = [], []
    provider = ScriptedProvider([tool_response(("list_files", {})), text_response("done")])
    agent = make_agent(provider, ctx)
    agent.on_message = seen.append
    agent.on_action = actions.append
    agent.run(convo())
    assert [m.role for m in seen] == ["assistant", "tool", "assistant"]
    assert actions[0].tool == "list_files"
    json.dumps([a.__dict__ for a in actions])  # serialisable


def test_call_interruptibly_returns_and_raises() -> None:
    from apexcore.platform_utils import call_interruptibly

    assert call_interruptibly(lambda: 42, lambda: None) == 42
    with pytest.raises(ValueError):
        call_interruptibly(lambda: (_ for _ in ()).throw(ValueError("x")), lambda: None)


def test_ctrl_c_interrupts_a_blocked_request() -> None:
    """Ctrl+C must work even while the request is stuck in a blocking read
    (which Windows cannot interrupt), via the worker-thread wait."""
    import _thread
    import threading
    import time

    from apexcore.platform_utils import call_interruptibly

    release = threading.Event()
    cancelled: list[bool] = []

    def blocked() -> str:
        release.wait(20)
        return "late"

    def cancel() -> None:
        cancelled.append(True)
        release.set()

    timer = threading.Timer(0.3, _thread.interrupt_main)
    timer.start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        call_interruptibly(blocked, cancel)
    assert cancelled and time.monotonic() - started < 10


def test_agent_cancels_provider_and_recovers(ctx: ToolContext) -> None:
    import _thread
    import threading

    class BlockingProvider(ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([text_response("second turn works")])
            self.release = threading.Event()
            self.cancels = 0
            self.first = True

        def complete(self, messages, *, model, tools=None, on_text=None):
            if self.first:
                self.first = False
                on_text("partial")
                self.release.wait(20)
                on_text(" more")  # arrives after cancellation: must not be shown
                raise RuntimeError("connection closed")
            return super().complete(messages, model=model, tools=tools, on_text=on_text)

        def cancel(self) -> None:
            self.cancels += 1
            self.release.set()

    provider = BlockingProvider()
    shown: list[str] = []

    class RecordingUI:
        def thinking(self):
            pass

        def assistant_text(self, text):
            shown.append(text)

        def assistant_done(self, message):
            pass

        def tool_started(self, call, summary):
            pass

        def tool_finished(self, record):
            pass

        def notice(self, text):
            pass

    agent = make_agent(provider, ctx)
    agent.ui = RecordingUI()
    conversation = convo()
    threading.Timer(0.3, _thread.interrupt_main).start()
    result = agent.run(conversation)
    assert result.status == "cancelled" and provider.cancels == 1
    assert shown == ["partial"]
    assert conversation[-1].content.startswith("partial")
    conversation.append(Message.user("again"))
    assert agent.run(conversation).text == "second turn works"
