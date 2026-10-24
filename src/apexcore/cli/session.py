"""Assemble everything one agent session needs (shared by chat, ask, and task)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from apexcore.agent.loop import ActionRecord, Agent, AgentUI, NullUI, TurnResult
from apexcore.agent.prompts import PromptInputs, build_system_prompt, personality_text
from apexcore.config import Settings, load_settings, read_config_file, write_config_file
from apexcore.context import ContextFile, load_persona, load_project_context
from apexcore.errors import ApexError, ConfigError
from apexcore.history import HistoryStore, Kind, Status
from apexcore.memory import MemoryStore
from apexcore.permissions import Approver, DenyAllApprover, PermissionPolicy
from apexcore.providers.base import Message, Provider, ToolCall, Usage
from apexcore.providers.factory import (
    ResolvedProvider,
    create_provider,
    provider_config,
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

COMPRESS_PROMPT = (
    "Summarize our conversation so far so we can continue it with less context. Include "
    "the user's goals and preferences, decisions made, files and commands involved and their "
    "current state, results of any checks, and open questions or next steps. Be specific and "
    "concise (under 300 words). Reply with the summary only."
)
SUMMARY_PREFIX = "[Summary of our earlier conversation]"


def stdin_is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def _repair_pairs(
    items: list[tuple[int | None, Message]],
) -> list[tuple[int | None, Message]]:
    out: list[tuple[int | None, Message]] = []
    pending: dict[str, ToolCall] = {}

    def flush() -> None:
        for call in pending.values():
            synthetic = Message.tool_result(
                call, "Not run: the session ended first.", is_error=True
            )
            out.append((None, synthetic))
        pending.clear()

    for message_id, message in items:
        if message.role == "tool":
            if pending.pop(message.tool_call_id or "", None) is not None:
                out.append((message_id, message))
            continue
        flush()
        out.append((message_id, message))
        if message.role == "assistant":
            pending.update({call.id: call for call in message.tool_calls})
    flush()
    return out


def repair_tool_sequence(messages: list[Message]) -> list[Message]:
    """Make a stored conversation valid to send again.

    If apex was killed between a model's tool calls and their results, the saved
    history has calls without results, which providers reject. Missing results
    are filled in, and results without a matching call are dropped.
    """
    return [message for _, message in _repair_pairs([(None, m) for m in messages])]


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


def estimate_tokens(messages: list[Message]) -> int:
    """A rough context-size estimate (about 4 characters per token)."""
    chars = 0
    for message in messages:
        chars += len(message.content or "")
        chars += sum(len(call.arguments) + len(call.name) for call in message.tool_calls)
    return chars // 4


def save_always_approved(argv: list[str]) -> None:
    """Add an exact command to permissions.always_allowed_commands in config.toml."""
    data = read_config_file()
    permissions = data.setdefault("permissions", {})
    approved = permissions.setdefault("always_allowed_commands", [])
    if argv not in approved:
        approved.append(list(argv))
    write_config_file(data)


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
    memory: MemoryStore | None = None
    persona: ContextFile | None = None
    project_context: ContextFile | None = None
    personality: str | None = None
    conversation: list[Message] = field(default_factory=list)
    # Row ids of the stored messages, parallel to ``conversation`` (None if unsaved).
    message_ids: list[int | None] = field(default_factory=list)
    conversation_id: str | None = None
    title: str | None = None
    usage: Usage = field(default_factory=Usage)
    usage_reported: bool = False
    compressions: int = 0
    _memory_snapshot: str = ""

    # --- Prompt ----------------------------------------------------------

    def system_prompt(self) -> str:
        return build_system_prompt(
            PromptInputs(
                tools=self.tools,
                workspace_root=str(self.workspace.root) if self.workspace else None,
                persona=self.persona,
                memory_block=self._memory_snapshot,
                project_context=self.project_context,
                personality=self.personality,
                custom_personalities=self.settings.agent.personalities,
            )
        )

    def refresh_system_prompt(self) -> None:
        if self.conversation:
            self.conversation[0] = Message.system(self.system_prompt())

    # --- History ---------------------------------------------------------

    def ensure_conversation(self, title: str) -> str | None:
        if self.store is None:
            return None
        if self.conversation_id is None:
            self.conversation_id = self.store.create_conversation(
                self.kind,
                self.title or title,
                provider=self.resolved.name,
                model=self.model,
                workspace=str(self.workspace.root) if self.workspace else None,
            )
            self.title = self.store.get_conversation(self.conversation_id).title
        return self.conversation_id

    def _persist(self, message: Message) -> None:
        message_id = None
        if self.store is not None and self.conversation_id is not None and message.role != "system":
            message_id = self.store.add_message(self.conversation_id, message)
        self.message_ids.append(message_id)

    def _persist_action(self, record: ActionRecord) -> None:
        if self.store is not None and self.conversation_id is not None:
            self.store.add_action(self.conversation_id, record.tool, record.summary, record.status)

    def _truncate(self, index: int) -> None:
        """Drop messages from ``index`` on, in memory and in the history file."""
        if self.store is not None:
            self.store.delete_messages([i for i in self.message_ids[index:] if i is not None])
        del self.conversation[index:]
        del self.message_ids[index:]

    # --- Conversation control ---------------------------------------------

    def reset(self, title: str | None = None) -> None:
        """Start a fresh conversation (keeps provider, model, and permissions)."""
        self._memory_snapshot = self.memory.render_for_prompt() if self.memory else ""
        self.conversation = [Message.system(self.system_prompt())]
        self.message_ids = [None]
        self.conversation_id = None
        self.title = title
        if self.ctx is not None:
            self.ctx.todos.clear()

    def last_user_index(self) -> int | None:
        for index in range(len(self.conversation) - 1, 0, -1):
            if self.conversation[index].role == "user":
                return index
        return None

    def undo(self) -> str | None:
        """Remove the last exchange; returns the removed user message."""
        index = self.last_user_index()
        if index is None:
            return None
        text = self.conversation[index].content
        self._truncate(index)
        return text

    def retry(self, ui: AgentUI) -> TurnResult | None:
        text = self.undo()
        if text is None:
            return None
        return self.send(text, ui)

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
            self._truncate(len(self.conversation) - 1)
        if self.store is not None and self.conversation_id is not None:
            self.store.update_conversation(
                self.conversation_id,
                status="active" if self.kind == "chat" else _STATUS_FOR_TURN[result.status],
                usage=result.usage,
                model=self.model,
            )
        return result

    def compress(self, ui: AgentUI | None = None, focus: str = "") -> int:
        """Replace the conversation with a model-written summary. Returns the number
        of messages summarized (0 if there was too little to compress)."""
        body = self.conversation[1:]
        if len(body) < 4:
            return 0
        prompt = COMPRESS_PROMPT + (f" Pay special attention to: {focus}" if focus else "")
        request = [*self.conversation, Message.user(prompt)]
        agent = Agent(
            provider=self.provider,
            model=self.model,
            tools=[],
            ctx=None,
            policy=self.policy,
            approver=self.approver,
            ui=ui or NullUI(),
            max_steps=1,
        )
        result = agent.run(request)
        self.usage = self.usage + result.usage
        self.usage_reported = self.usage_reported or result.usage_reported
        if result.status == "cancelled":
            raise KeyboardInterrupt
        if result.status != "completed" or not result.text.strip():
            if result.error is not None:
                raise result.error
            raise ApexError("The model returned an empty summary; nothing was changed.")
        old_title = self.title
        self.compressions += 1
        self.reset(title=f"{old_title or 'chat'} #{self.compressions + 1}")
        self.ensure_conversation(self.title or "chat")
        for message in (
            Message.user(f"{SUMMARY_PREFIX}\n{result.text.strip()}"),
            Message.assistant("Thanks, I have the summary and I'm ready to continue."),
        ):
            self.conversation.append(message)
            self._persist(message)
        return len(body)

    def load_history(self, conversation_id: str) -> None:
        if self.store is None:
            return
        pairs = [
            (message_id, message)
            for message_id, message in self.store.get_messages_with_ids(conversation_id)
            if message.role != "system"
        ]
        repaired = _repair_pairs(pairs)
        self.conversation = [self.conversation[0], *(m for _, m in repaired)]
        self.message_ids = [None, *(i for i, _ in repaired)]
        self.conversation_id = conversation_id
        self.title = self.store.get_conversation(conversation_id).title

    def rename(self, title: str) -> str:
        self.title = " ".join(title.split())[:120]
        if self.store is not None and self.conversation_id is not None:
            self.title = self.store.rename(self.conversation_id, title)
        return self.title

    # --- Model and personality --------------------------------------------

    def set_model(self, model: str) -> None:
        self.model = model

    def switch_provider(self, name: str, model: str | None = None) -> None:
        config = provider_config(self.settings, name)
        chosen = model or config.model
        if not chosen:
            raise ConfigError(
                f"No model given for provider {name!r}.", hint=f"Use /model {name}:<model-id>."
            )
        new_provider = create_provider(name, config, redactor=GLOBAL_REDACTOR)
        self.provider.close()
        self.provider = new_provider
        self.resolved = ResolvedProvider(name=name, config=config, model=chosen)
        self.model = chosen

    def set_personality(self, name: str | None) -> None:
        if name and name.lower() not in ("none", "default", "neutral"):
            if personality_text(name, self.settings.agent.personalities) is None:
                raise ConfigError(f"Unknown personality {name!r}.")
            self.personality = name
        else:
            self.personality = None
        self.refresh_system_prompt()

    def context_tokens(self) -> int:
        return estimate_tokens(self.conversation)

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
    auto: bool = False,
) -> AgentSession:
    settings = settings or load_settings()
    resolved = resolve_provider(settings, provider, model)
    chosen_model = require_model(resolved)
    interactive = stdin_is_interactive() if interactive is None else interactive
    policy = PermissionPolicy(
        settings.permissions,
        read_only=read_only,
        interactive=interactive,
        auto=auto,
        on_always=save_always_approved,
    )
    memory = MemoryStore() if settings.memory.enabled else None
    session_tools = kind != "ask"

    tools: list[Tool] = []
    ws: Workspace | None = None
    ctx: ToolContext | None = None
    if tool_mode != "none" and resolved.config.supports_tools:
        risks = {Risk.READ}
        if tool_mode == "full":
            risks |= {Risk.WRITE, Risk.EXECUTE}
        if memory is not None and session_tools:
            risks.add(Risk.MEMORY)
        # Do not offer the model tools the policy would always refuse.
        risks = {risk for risk in risks if policy.static_decision(risk).value != "deny"}
        risks.add(Risk.READ)
        tools = build_tools(risks, session_tools=session_tools)
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
            memory=memory if session_tools else None,
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
        memory=memory,
        persona=load_persona(),
        project_context=(
            load_project_context(ws) if ws is not None and settings.agent.context_files else None
        ),
        personality=settings.agent.personality,
    )
    session.reset()
    return session
