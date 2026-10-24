"""Assemble everything one agent session needs (shared by chat, ask, and task)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from apexcore.agent.loop import ActionRecord, Agent, AgentUI, TurnResult
from apexcore.agent.prompts import build_system_prompt
from apexcore.config import Settings, load_settings
from apexcore.history import HistoryStore, Kind, Status
from apexcore.permissions import Approver, DenyAllApprover, PermissionPolicy
from apexcore.providers.base import Message, Provider, ToolCall, Usage
from apexcore.providers.factory import (
    ResolvedProvider,
    create_provider,
    require_model,
    resolve_provider,
)
from apexcore.redaction import GLOBAL_REDACTOR
from apexcore.tools.base import Risk, Tool, ToolContext
from apexcore.tools.registry import build_tools
from apexcore.tools.workspace import Workspace

ToolMode = Literal["none", "read", "full"]

_STATUS_FOR_TURN: dict[str, Status] = {
    "completed": "completed",
    "max_steps": "max_steps",
    "cancelled": "cancelled",
    "failed": "failed",
}


def stdin_is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def repair_tool_sequence(messages: list[Message]) -> list[Message]:
    """Make a stored conversation valid to send again.

    If apex was killed between a model's tool calls and their results, the saved
    history has calls without results, which providers reject. Missing results
    are filled in, and results without a matching call are dropped.
    """
    out: list[Message] = []
    pending: dict[str, ToolCall] = {}

    def flush() -> None:
        for call in pending.values():
            out.append(
                Message.tool_result(call, "Not run: the session ended first.", is_error=True)
            )
        pending.clear()

    for message in messages:
        if message.role == "tool":
            if pending.pop(message.tool_call_id or "", None) is not None:
                out.append(message)
            continue
        flush()
        out.append(message)
        if message.role == "assistant":
            pending.update({call.id: call for call in message.tool_calls})
    flush()
    return out


def workspace_warning(root: Path) -> str | None:
    if root == Path(root.anchor):
        return f"The workspace is the filesystem root ({root}). Consider a project directory."
    try:
        if root == Path.home().resolve():
            return (
                "The workspace is your home directory, so the agent can read most of your "
                "files. Consider running apex inside a project directory."
            )
    except (OSError, RuntimeError):
        pass
    return None


@dataclass
class AgentSession:
    kind: Kind
    settings: Settings
    resolved: ResolvedProvider
    provider: Provider
    model: str
    tools: list[Tool]
    workspace: Workspace | None
    ctx: ToolContext | None
    policy: PermissionPolicy
    approver: Approver
    store: HistoryStore | None
    conversation: list[Message] = field(default_factory=list)
    conversation_id: str | None = None
    usage: Usage = field(default_factory=Usage)
    usage_reported: bool = False

    # --- History ---------------------------------------------------------

    def ensure_conversation(self, title: str) -> str | None:
        if self.store is None:
            return None
        if self.conversation_id is None:
            self.conversation_id = self.store.create_conversation(
                self.kind,
                title,
                provider=self.resolved.name,
                model=self.model,
                workspace=str(self.workspace.root) if self.workspace else None,
            )
        return self.conversation_id

    def _persist(self, message: Message) -> None:
        if self.store is not None and self.conversation_id is not None and message.role != "system":
            self.store.add_message(self.conversation_id, message)

    def _persist_action(self, record: ActionRecord) -> None:
        if self.store is not None and self.conversation_id is not None:
            self.store.add_action(self.conversation_id, record.tool, record.summary, record.status)

    # --- Turns -----------------------------------------------------------

    def reset(self) -> None:
        """Start a fresh conversation (keeps provider, model, and permissions)."""
        root = str(self.workspace.root) if self.workspace else None
        self.conversation = [Message.system(build_system_prompt(self.tools, root))]
        self.conversation_id = None

    def send(self, text: str, ui: AgentUI) -> TurnResult:
        self.ensure_conversation(text)
        user = Message.user(text)
        self.conversation.append(user)
        self._persist(user)
        agent = Agent(
            provider=self.provider,
            model=self.model,
            tools=self.tools,
            ctx=self.ctx,
            policy=self.policy,
            approver=self.approver,
            ui=ui,
            max_steps=self.settings.agent.max_steps,
            on_message=self._persist,
            on_action=self._persist_action,
        )
        result = agent.run(self.conversation)
        self.usage = self.usage + result.usage
        self.usage_reported = self.usage_reported or result.usage_reported
        if result.status == "failed" and self.conversation and self.conversation[-1] is user:
            # Nothing was answered; drop the message so a retry does not send it twice.
            self.conversation.pop()
        if self.store is not None and self.conversation_id is not None:
            self.store.update_conversation(
                self.conversation_id,
                status="active" if self.kind == "chat" else _STATUS_FOR_TURN[result.status],
                usage=result.usage,
                model=self.model,
            )
        return result

    def load_history(self, conversation_id: str) -> None:
        if self.store is None:
            return
        messages = [m for m in self.store.get_messages(conversation_id) if m.role != "system"]
        self.conversation = self.conversation[:1] + repair_tool_sequence(messages)
        self.conversation_id = conversation_id

    def close(self) -> None:
        self.provider.close()


def open_session(
    *,
    kind: Kind,
    provider: str | None = None,
    model: str | None = None,
    workspace: Path | None = None,
    tool_mode: ToolMode = "full",
    read_only: bool = False,
    no_history: bool = False,
    approver: Approver | None = None,
    interactive: bool | None = None,
    settings: Settings | None = None,
) -> AgentSession:
    settings = settings or load_settings()
    resolved = resolve_provider(settings, provider, model)
    chosen_model = require_model(resolved)
    interactive = stdin_is_interactive() if interactive is None else interactive
    policy = PermissionPolicy(settings.permissions, read_only=read_only, interactive=interactive)

    tools: list[Tool] = []
    ws: Workspace | None = None
    ctx: ToolContext | None = None
    if tool_mode != "none" and resolved.config.supports_tools:
        risks = {Risk.READ}
        if tool_mode == "full":
            risks |= {Risk.WRITE, Risk.EXECUTE}
        # Do not offer the model tools the policy would always refuse.
        risks = {risk for risk in risks if policy.static_decision(risk).value != "deny"}
        risks.add(Risk.READ)
        tools = build_tools(risks)
        ws = Workspace(
            workspace or Path.cwd(),
            extra_secret_patterns=settings.tools.extra_secret_patterns,
            extra_ignore_dirs=settings.tools.extra_ignore_dirs,
        )
        secret_envs = frozenset(
            cfg.api_key_env for cfg in settings.providers.values() if cfg.api_key_env
        ) | ({resolved.config.api_key_env} if resolved.config.api_key_env else set())
        ctx = ToolContext(
            workspace=ws,
            tool_settings=settings.tools,
            permission_settings=settings.permissions,
            redactor=GLOBAL_REDACTOR,
            secret_env_names=frozenset(secret_envs),
        )

    provider_obj = create_provider(resolved.name, resolved.config, redactor=GLOBAL_REDACTOR)
    store = None if (no_history or not settings.history.enabled) else HistoryStore()
    session = AgentSession(
        kind=kind,
        settings=settings,
        resolved=resolved,
        provider=provider_obj,
        model=chosen_model,
        tools=tools,
        workspace=ws,
        ctx=ctx,
        policy=policy,
        approver=approver if (approver is not None and interactive) else DenyAllApprover(),
        store=store,
    )
    session.reset()
    return session
