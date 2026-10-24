"""Exception hierarchy.

Every error that is meant to reach the user derives from :class:`ApexError` and
carries a short message plus an optional hint describing how to fix it. Messages
must never contain secrets; callers pass provider output through the redactor
before building an error.
"""

from __future__ import annotations


class ApexError(Exception):
    """Base class for user-facing errors."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message


class ConfigError(ApexError):
    """Configuration is missing, unreadable, or invalid."""


class HistoryError(ApexError):
    """The local history database could not be used."""


# --- Provider errors -------------------------------------------------------


class ProviderError(ApexError):
    """A model provider request failed."""

    retryable = False


class AuthenticationError(ProviderError):
    """The provider rejected the credentials (HTTP 401/403) or none were found."""


class RateLimitError(ProviderError):
    """The provider rate-limited the request (HTTP 429)."""

    retryable = True

    def __init__(
        self, message: str, *, hint: str | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message, hint=hint)
        self.retry_after = retry_after


class QuotaExceededError(ProviderError):
    """The account has no remaining quota or credit. Retrying will not help."""


class ModelNotFoundError(ProviderError):
    """The requested model does not exist or is not available to this key."""


class BadRequestError(ProviderError):
    """The provider rejected the request as invalid (HTTP 400/422)."""


class ProviderUnavailableError(ProviderError):
    """The provider returned a server error or reported being overloaded."""

    retryable = True


class NetworkError(ProviderError):
    """The provider could not be reached, or the connection timed out."""

    retryable = True


class ProviderResponseError(ProviderError):
    """The provider returned a response APEX CORE could not understand."""


# --- Tool errors -----------------------------------------------------------


class ToolError(ApexError):
    """A tool could not run. The message is returned to the model as the tool result."""


class WorkspaceError(ToolError):
    """A path is outside the workspace or otherwise not allowed."""


class CommandBlockedError(ToolError):
    """A command was refused by the execution policy."""
