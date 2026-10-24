"""Heuristic checks for text that will be placed in the system prompt.

Memory entries and project context files (``AGENTS.md`` and similar) reach the
model with more authority than ordinary tool output, so they are scanned for
the most common prompt-injection shapes before use: instruction overrides,
deception, hidden HTML comments, invisible Unicode, pipe-to-shell commands, and
credential exfiltration. This is a speed bump, not a guarantee; see SECURITY.md.
"""

from __future__ import annotations

import re

# Zero-width and bidirectional-control characters can hide instructions from a
# human reviewer while the model still reads them.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(ignore|disregard|forget|override)\b\W+(?:\w+\W+){0,3}?"
            r"(previous|prior|above|earlier|preceding|system|your|all)\b\W+(?:\w+\W+){0,2}?"
            r"(instructions?|prompts?|directives?)\b",
            re.IGNORECASE,
        ),
        "instruction override",
    ),
    (
        re.compile(
            r"\byou are now\b|\bnew system prompt\b|\bdeveloper mode\b|\bjailbr(?:eak|oken)\b",
            re.IGNORECASE,
        ),
        "role hijack",
    ),
    (
        re.compile(
            r"\b(?:do not|don't|never)\s+(?:tell|inform|mention|reveal|show)\b[^.\n]{0,40}"
            r"\b(?:user|human|operator)\b",
            re.IGNORECASE,
        ),
        "deception",
    ),
    (re.compile(r"<!--.*?-->", re.DOTALL), "hidden HTML comment"),
    (
        re.compile(
            r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n]{0,200}\|\s*(?:ba|z|da|k)?sh\b", re.I
        ),
        "pipe-to-shell command",
    ),
    (
        re.compile(
            r"\b(?:send|post|upload|exfiltrat\w*|forward|leak)\b[^.\n]{0,60}"
            r"(?:api[_ -]?keys?|tokens?|secrets?|passwords?|credentials?|\.env\b|id_rsa|\.ssh)",
            re.IGNORECASE,
        ),
        "credential exfiltration",
    ),
    (
        re.compile(r"authorized_keys|/etc/shadow|\.aws/credentials|\.ssh/id_\w+", re.IGNORECASE),
        "secret file access",
    ),
)


def has_invisible_characters(text: str) -> bool:
    return bool(_INVISIBLE.search(text))


def scan_text(text: str) -> list[str]:
    """Return the names of suspicious patterns found in ``text`` (empty if none)."""
    findings: list[str] = []
    if has_invisible_characters(text):
        findings.append("invisible characters")
    for pattern, label in _PATTERNS:
        if pattern.search(text) and label not in findings:
            findings.append(label)
    return findings
