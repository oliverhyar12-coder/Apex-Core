"""Extra instructions for the system prompt: project context files and the persona.

* **Project context**: the first of ``APEX.md``, ``AGENTS.md``, ``CLAUDE.md`` or
  ``.cursorrules`` found in the workspace root. It comes from the repository, so
  it is treated as untrusted: it is size-capped, scanned for prompt injection
  (and blocked if anything is found), and placed in a clearly labelled section
  that cannot override the safety rules.
* **Persona** (``SOUL.md`` in the configuration directory): your own file that
  sets the agent's identity and tone. It is trusted, but still scanned so you
  are warned about anything suspicious.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from apexcore import paths
from apexcore.errors import WorkspaceError
from apexcore.safety import scan_text
from apexcore.tools.workspace import Workspace

CONTEXT_FILE_NAMES = ("APEX.md", "AGENTS.md", "CLAUDE.md", ".cursorrules")
PERSONA_FILE_NAME = "SOUL.md"
MAX_CONTEXT_CHARS = 20_000
MAX_READ_BYTES = 1_000_000


@dataclass
class ContextFile:
    name: str
    text: str
    blocked: bool = False
    findings: list[str] = field(default_factory=list)
    truncated: bool = False


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head, tail = int(limit * 0.7), int(limit * 0.2)
    omitted = len(text) - head - tail
    marker = f"\n\n[... {omitted:,} characters truncated; read the file for the rest ...]\n\n"
    return text[:head] + marker + text[-tail:], True


def _read(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_READ_BYTES)
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()


def load_project_context(workspace: Workspace) -> ContextFile | None:
    """The workspace's instructions file, if any (only the workspace root is checked)."""
    for name in CONTEXT_FILE_NAMES:
        candidate = workspace.root / name
        if not candidate.is_file():
            continue
        try:
            resolved = workspace.resolve(name)  # rejects symlinks that escape the workspace
        except WorkspaceError:
            continue
        text = _read(resolved)
        if not text:
            continue
        findings = scan_text(text)
        if findings:
            return ContextFile(name=name, text="", blocked=True, findings=findings)
        body, truncated = _truncate(text, MAX_CONTEXT_CHARS)
        return ContextFile(name=name, text=body, truncated=truncated)
    return None


def persona_path() -> Path:
    return paths.config_dir() / PERSONA_FILE_NAME


def load_persona() -> ContextFile | None:
    path = persona_path()
    if not path.is_file():
        return None
    text = _read(path)
    if not text:
        return None
    body, truncated = _truncate(text, MAX_CONTEXT_CHARS)
    # Your own file: load it, but report anything suspicious so you can check it.
    return ContextFile(
        name=PERSONA_FILE_NAME, text=body, findings=scan_text(text), truncated=truncated
    )
