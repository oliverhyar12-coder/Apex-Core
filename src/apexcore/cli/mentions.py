"""``@path`` in a chat message attaches that workspace file to the message.

The same rules as the read_file tool apply: the file must be inside the
workspace, not in ``.git``, not a credentials file, and a text file. Its
contents are redacted like tool output and sent to the provider with the message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apexcore.errors import ToolError, WorkspaceError
from apexcore.redaction import Redactor
from apexcore.tools.files import read_text
from apexcore.tools.workspace import Workspace

# "@" at the start of the message or after whitespace, so e-mail addresses
# (me@example.com) are not mentions.
MENTION = re.compile(r"(?<!\S)@(\S+)")
MAX_ATTACH_BYTES = 100_000
_TRAILING = ".,;:!?)]}\"'"


@dataclass(frozen=True)
class Attachment:
    path: str
    size: int
    truncated: bool


def attach_files(
    text: str, workspace: Workspace, redactor: Redactor
) -> tuple[str, list[Attachment], list[str]]:
    """Return the message with mentioned files appended, what was attached, and
    one line per mention that could not be attached."""
    attachments: list[Attachment] = []
    problems: list[str] = []
    blocks: list[str] = []
    seen: set[str] = set()
    for match in MENTION.finditer(text):
        raw = match.group(1).rstrip(_TRAILING)
        if not raw or raw in seen:
            continue
        seen.add(raw)
        try:
            path = workspace.resolve(raw)
        except WorkspaceError as exc:
            problems.append(f"@{raw}: {exc}")
            continue
        if not path.is_file():
            if path.is_dir():
                problems.append(f"@{raw} is a folder; mention a file inside it.")
            elif "/" in raw or "." in raw:
                problems.append(f"@{raw}: no such file in the workspace.")
            continue  # e.g. "@alice": not meant as a file
        try:
            workspace.check_not_secret(path)
            content, truncated = read_text(path, MAX_ATTACH_BYTES)
        except (WorkspaceError, ToolError) as exc:
            problems.append(f"@{raw}: {exc}")
            continue
        rel = workspace.relative(path)
        note = " truncated" if truncated else ""
        blocks.append(
            f'<attached_file path="{rel}"{note}>\n{redactor.redact(content)}\n</attached_file>'
        )
        attachments.append(Attachment(rel, len(content.encode("utf-8")), truncated))
    if blocks:
        text = text + "\n\n" + "\n\n".join(blocks)
    return text, attachments, problems
