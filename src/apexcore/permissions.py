"""Permission policy and approval prompts.

Default policy:

=========  ===========================================================
read       allowed (inside the workspace, credentials files excluded)
write      ask before every change, showing a diff
execute    ask before every command; ``permissions.auto_approve_commands``
           can pre-approve exact argv prefixes such as ``["git", "status"]``,
           except for commands flagged destructive or opaque
=========  ===========================================================

``--read-only`` denies write and execute outright. When there is no
interactive terminal to ask, "ask" becomes "deny". Memory notes (the agent's
own files, never the workspace) are allowed by default (``permissions.memory``).

At the prompt you can allow an action once, for the rest of the session, or
(for an exact command) always, which saves it to ``always_allowed_commands``.
Unlike ``auto_approve_commands``, those match only the identical argv.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from apexcore.config import PermissionSettings
from apexcore.tools.base import PreparedAction, Risk, Tool


class Decision(enum.Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


Scope = Literal["once", "session", "always"]


@dataclass
class ApprovalRequest:
    tool: str
    risk: Risk
    summary: str
    details: str = ""
    warnings: list[str] = field(default_factory=list)
    # Whether "allow for this session" may be offered (never for flagged commands).
    can_remember: bool = True
    # Whether "always" (saved to config.toml) may be offered: exact commands only.
    can_always: bool = False


@dataclass
class ApprovalResponse:
    approved: bool
    scope: Scope = "once"
    feedback: str | None = None


class Approver(Protocol):
    def confirm(self, request: ApprovalRequest) -> ApprovalResponse: ...


class DenyAllApprover:
    """Used when no terminal is available: every request is refused."""

    def confirm(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(approved=False)


def _prefix_match(argv: list[str], prefix: list[str]) -> bool:
    return len(argv) >= len(prefix) and argv[: len(prefix)] == prefix


class PermissionPolicy:
    def __init__(
        self,
        settings: PermissionSettings,
        *,
        read_only: bool = False,
        interactive: bool = True,
        auto: bool = False,
        on_always: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.read_only = read_only
        self.interactive = interactive
        # Session "auto" mode (`/auto`, `--auto`): approve edits and ordinary
        # commands without asking. Flagged commands (destructive or opaque) still
        # ask, and blocked commands are still refused.
        self.auto = auto
        # Called with an argv the user chose to allow "always", to save it.
        self.on_always = on_always
        self._session_writes = False
        self._session_commands: list[list[str]] = []

    @property
    def auto_active(self) -> bool:
        # Auto mode relies on someone watching the terminal; without one it
        # approves nothing, so `--auto` in a pipe or CI job still refuses.
        return self.auto and self.interactive

    def static_decision(self, risk: Risk) -> Decision:
        """Policy for a risk level before looking at a specific action."""
        if risk is Risk.READ:
            return Decision.ALLOW
        if risk is Risk.MEMORY:
            decision = Decision(self.settings.memory)
        elif self.read_only:
            return Decision.DENY
        else:
            level = self.settings.write if risk is Risk.WRITE else self.settings.execute
            decision = Decision(level)
        if decision is Decision.ASK and not self.interactive:
            return Decision.DENY
        return decision

    def decide(self, tool: Tool, action: PreparedAction) -> tuple[Decision, str]:
        if tool.risk is Risk.READ:
            return Decision.ALLOW, "read-only tool"
        if tool.risk is Risk.MEMORY:
            if self.settings.memory == "deny":
                return Decision.DENY, "memory writes are disabled (permissions.memory = deny)"
            if self.settings.memory == "allow" or self.auto_active:
                return Decision.ALLOW, "memory writes are allowed"
        elif self.read_only:
            return Decision.DENY, "this session is read-only (--read-only)"
        elif tool.risk is Risk.WRITE:
            if self.settings.write == "deny":
                return Decision.DENY, "file edits are disabled (permissions.write = deny)"
            if self.settings.write == "allow" or self._session_writes:
                return Decision.ALLOW, "file edits are pre-approved"
            if self.auto_active:
                return Decision.ALLOW, "auto mode"
        else:
            if self.settings.execute == "deny":
                return Decision.DENY, "commands are disabled (permissions.execute = deny)"
            argv = action.argv or []
            if action.auto_approvable and argv:
                if any(_prefix_match(argv, p) for p in self.settings.auto_approve_commands):
                    return Decision.ALLOW, "matches permissions.auto_approve_commands"
                if argv in self.settings.always_allowed_commands:
                    return Decision.ALLOW, "you chose to always allow this command"
                if argv in self._session_commands:
                    return Decision.ALLOW, "approved earlier in this session"
                if self.auto_active:
                    return Decision.ALLOW, "auto mode"
        if not self.interactive:
            return Decision.DENY, "approval required, but there is no interactive terminal"
        return Decision.ASK, "approval required"

    def remember(self, tool: Tool, action: PreparedAction, scope: Scope = "session") -> None:
        """Record a "session" or "always" approval from the prompt."""
        if scope == "once":
            return
        if tool.risk is Risk.WRITE:
            self._session_writes = True
        elif tool.risk is Risk.EXECUTE and action.argv and action.auto_approvable:
            argv = list(action.argv)
            self._session_commands.append(argv)
            if scope == "always" and self.on_always is not None:
                self.on_always(argv)
