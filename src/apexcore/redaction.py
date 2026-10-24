"""Best-effort secret redaction.

Two levels are provided:

* :meth:`Redactor.redact` removes values the redactor knows about (for example
  the API key in use) and tokens whose *format* is unambiguous (``sk-...``,
  ``ghp_...``, PEM private keys, ...). It is safe to apply to file contents and
  command output, because it does not touch ordinary source code.
* :meth:`Redactor.redact_aggressive` additionally masks ``key = value`` style
  assignments and ``Authorization`` headers. It is used for error messages and
  diagnostics, where a false positive costs nothing.

Redaction is a safety net, not a guarantee: secrets in unknown formats can
still slip through. See SECURITY.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"

# Token formats that are unambiguous enough to redact anywhere.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----",
        re.DOTALL,
    ),
    # OpenAI / Anthropic / OpenRouter / DeepSeek style keys.
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{50,}"),
)

_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}")
_HEADER = re.compile(r"(?i)\b(x-api-key|api-key|authorization)(\s*[:=]\s*)([^\s,;]+)")
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (["']?\b[a-z0-9_.\-]*(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)["']?
     \s*[:=]\s*["']?)
    ([^\s"',;}{]{8,})
    """
)

_MIN_KNOWN_SECRET_LENGTH = 8


class Redactor:
    """Masks secrets in text before it is displayed, logged, stored, or sent."""

    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        self._secrets: set[str] = set()
        for secret in secrets:
            self.add(secret)

    def add(self, secret: str | None) -> None:
        """Register an exact value to mask. Very short values are ignored."""
        if secret and len(secret.strip()) >= _MIN_KNOWN_SECRET_LENGTH:
            self._secrets.add(secret.strip())

    @property
    def known_count(self) -> int:
        return len(self._secrets)

    def redact(self, text: str) -> str:
        """Mask known secrets and unambiguous token formats."""
        if not text:
            return text
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTED)
        for pattern in _TOKEN_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def redact_aggressive(self, text: str) -> str:
        """Like :meth:`redact`, but also masks headers and ``key=value`` assignments."""
        text = self.redact(text)
        if not text:
            return text
        text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
        text = _HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        return _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{REDACTED}", text)


# A process-wide redactor that knows every credential APEX CORE has loaded.
GLOBAL_REDACTOR = Redactor()
