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
interactive terminal to ask, "ask" becomes "deny".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Protocol

from apexcore.config import PermissionSettings
from apexcore.tools.base import PreparedAction, Risk, Tool


class Decision(enum.Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class ApprovalRequest:
    tool: str
    risk: Risk
    summary: str
    details: str = ""
    warnings: list[str] = field(default_factory=list)
    # Whether "always allow for this session" may be offered.
    can_remember: bool = True


@dataclass
class ApprovalResponse:
    approved: bool
    remember: bool = False
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
    ) -> None:
        self.settings = settings
        self.read_only = read_only
        self.interactive = interactive
        # Session-scoped "always allow" grants from the approval prompt.
        self._session_writes = False
        self._session_commands: list[list[str]] = []

    def static_decision(self, risk: Risk) -> Decision:
        """Policy for a risk level before looking at a specific action."""
        if risk is Risk.READ:
            return Decision.ALLOW
        if self.read_only:
            return Decision.DENY
        level = self.settings.write if risk is Risk.WRITE else self.settings.execute
        decision = Decision(level)
        if decision is Decision.ASK and not self.interactive:
            return Decision.DENY
        return decision

    def decide(self, tool: Tool, action: PreparedAction) -> tuple[Decision, str]:
        if tool.risk is Risk.READ:
            return Decision.ALLOW, "read-only tool"
        if self.read_only:
            return Decision.DENY, "this session is read-only (--read-only)"
        if tool.risk is Risk.WRITE:
            if self.settings.write == "deny":
                return Decision.DENY, "file edits are disabled (permissions.write = deny)"
            if self.settings.write == "allow" or self._session_writes:
                return Decision.ALLOW, "file edits are pre-approved"
        else:
            if self.settings.execute == "deny":
                return Decision.DENY, "commands are disabled (permissions.execute = deny)"
            argv = action.argv or []
            if action.auto_approvable and argv:
                if any(_prefix_match(argv, p) for p in self.settings.auto_approve_commands):
                    return Decision.ALLOW, "matches permissions.auto_approve_commands"
                if argv in self._session_commands:
                    return Decision.ALLOW, "approved earlier in this session"
        if not self.interactive:
            return Decision.DENY, "approval required, but there is no interactive terminal"
        return Decision.ASK, "approval required"

    def remember(self, tool: Tool, action: PreparedAction) -> None:
        if tool.risk is Risk.WRITE:
            self._session_writes = True
        elif tool.risk is Risk.EXECUTE and action.argv and action.auto_approvable:
            self._session_commands.append(list(action.argv))
