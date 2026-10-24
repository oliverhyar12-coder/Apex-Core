from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from apexcore.errors import ToolError, WorkspaceError
from apexcore.tools.base import ToolContext
from apexcore.tools.files import ListFilesTool, ReadFileTool, SearchFilesTool
from apexcore.tools.workspace import Workspace


def run(tool, ctx: ToolContext, **raw) -> str:
    action = tool.prepare(tool.parse_args(raw), ctx)
    return action.run().content


# --- Containment -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["../outside.txt", "../../etc/passwd", "sub/../../outside.txt", "~/x", "a\x00b"],
)
def test_traversal_is_rejected(ws: Workspace, raw: str) -> None:
    (ws.root / "sub").mkdir()
    with pytest.raises(WorkspaceError):
        ws.resolve(raw)


def test_absolute_paths(ws: Workspace, tmp_path: Path) -> None:
    inside = ws.root / "a.txt"
    inside.write_text("x", encoding="utf-8")
    assert ws.resolve(str(inside)) == inside.resolve()
    with pytest.raises(WorkspaceError, match="outside the workspace"):
        ws.resolve(str(tmp_path / "elsewhere.txt"))
    with pytest.raises(WorkspaceError):
        ws.resolve(Path(ws.root.anchor).as_posix())


def test_git_directory_is_blocked(ws: Workspace) -> None:
    (ws.root / ".git").mkdir()
    (ws.root / ".git" / "config").write_text("[core]", encoding="utf-8")
    for raw in (".git", ".git/config", "./.git/../.git/config", ".GIT/config"):
        with pytest.raises(WorkspaceError, match=r"\.git"):
            ws.resolve(raw)


def test_symlink_escape_is_blocked(ws: Workspace, tmp_path: Path, symlinks: None) -> None:
    secret_dir = tmp_path / "private"
    secret_dir.mkdir()
    (secret_dir / "id.txt").write_text("secret", encoding="utf-8")
    (ws.root / "link-dir").symlink_to(secret_dir, target_is_directory=True)
    (ws.root / "link-file").symlink_to(secret_dir / "id.txt")
    for raw in ("link-dir/id.txt", "link-file", "link-dir"):
        with pytest.raises(WorkspaceError, match="outside"):
            ws.resolve(raw)


def test_symlink_inside_workspace_is_allowed(ws: Workspace, symlinks: None) -> None:
    (ws.root / "real.txt").write_text("hello", encoding="utf-8")
    (ws.root / "alias.txt").symlink_to(ws.root / "real.txt")
    assert ws.resolve("alias.txt") == (ws.root / "real.txt").resolve()


def test_symlinked_secret_is_still_secret(ws: Workspace, ctx: ToolContext, symlinks: None) -> None:
    (ws.root / ".env").write_text("TOKEN=abc", encoding="utf-8")
    (ws.root / "notes.txt").symlink_to(ws.root / ".env")
    with pytest.raises(WorkspaceError, match="credentials"):
        ReadFileTool().prepare(ReadFileTool().parse_args({"path": "notes.txt"}), ctx)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")
@pytest.mark.parametrize("raw", ["file.txt:stream", "CON", "nul.txt", "sub\\aux", "C:foo"])
def test_windows_special_paths(ws: Workspace, raw: str) -> None:
    with pytest.raises(WorkspaceError):
        ws.resolve(raw)


@pytest.mark.parametrize(
    "raw", ["file.txt:stream", "CON", "nul.txt", "sub\\aux", "C:foo", "LPT1.log", "a/com3"]
)
def test_windows_rules_portably(ws: Workspace, raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows-only path checks, exercised on every OS."""
    monkeypatch.setattr("apexcore.tools.workspace._is_windows", lambda: True)
    with pytest.raises(WorkspaceError):
        ws._check_raw(raw)


def test_windows_rules_allow_normal_names(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apexcore.tools.workspace._is_windows", lambda: True)
    for raw in ("src/console.py", "context.txt", "C:\\Users\\x\\file.txt", "\\\\server\\share\\f"):
        ws._check_raw(raw)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")
def test_windows_case_insensitive_containment(ws: Workspace) -> None:
    (ws.root / "File.txt").write_text("x", encoding="utf-8")
    upper = str(ws.root).upper() + "\\FILE.TXT"
    assert ws.resolve(upper).name.lower() == "file.txt"


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        (".env", True),
        (".env.local", True),
        ("prod.env", True),
        ("server.pem", True),
        ("id_rsa", True),
        ("id_ed25519.pub", True),
        (".npmrc", True),
        ("credentials.json", True),
        (".env.example", False),
        (".env.sample", False),
        ("environment.py", False),
        ("keys.py", False),
        ("README.md", False),
        ("SECRETS.YML", True),
    ],
)
def test_secret_detection(ws: Workspace, name: str, secret: bool) -> None:
    assert ws.is_secret(ws.root / name) is secret


def test_extra_secret_patterns(tmp_path: Path) -> None:
    ws = Workspace(tmp_path, extra_secret_patterns=["*.secret"])
    assert ws.is_secret(tmp_path / "db.SECRET")


def test_workspace_must_exist(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "missing")
    (tmp_path / "file").write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "file")


# --- list_files ----------------------------------------------------------------


def test_list_files(ws: Workspace, ctx: ToolContext) -> None:
    (ws.root / "src").mkdir()
    (ws.root / "src" / "main.py").write_text("print()", encoding="utf-8")
    (ws.root / "README.md").write_text("hi", encoding="utf-8")
    (ws.root / ".git").mkdir()
    (ws.root / "node_modules").mkdir()
    (ws.root / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    flat = run(ListFilesTool(), ctx).splitlines()
    assert flat == ["node_modules/", "README.md", "src/"]
    deep = run(ListFilesTool(), ctx, recursive=True).splitlines()
    assert deep == ["README.md", "src/main.py"]
    assert run(ListFilesTool(), ctx, path="src") == "src/main.py"


def test_list_files_limits(ws: Workspace, ctx: ToolContext) -> None:
    for i in range(5):
        (ws.root / f"f{i}.txt").write_text("x", encoding="utf-8")
    out = run(ListFilesTool(), ctx, max_entries=2)
    assert "stopped after 2 entries" in out
    with pytest.raises(ToolError):
        ListFilesTool().prepare(ListFilesTool().parse_args({"path": "f0.txt"}), ctx)


# --- read_file -----------------------------------------------------------------


def test_read_file_with_line_numbers(ws: Workspace, ctx: ToolContext) -> None:
    (ws.root / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    out = run(ReadFileTool(), ctx, path="a.py")
    assert out.splitlines()[0] == "a.py (lines 1-3 of 3)"
    assert "     2\ttwo" in out
    partial = run(ReadFileTool(), ctx, path="a.py", start_line=2, max_lines=1)
    assert "     2\ttwo" in partial and "three" not in partial.split("\n", 2)[1]
    assert "use start_line=3" in partial


def test_read_file_handles_encodings(ws: Workspace, ctx: ToolContext) -> None:
    (ws.root / "bom.txt").write_bytes("﻿héllo\r\nwörld".encode())
    out = run(ReadFileTool(), ctx, path="bom.txt")
    assert "héllo" in out and "wörld" in out and "﻿" not in out
    (ws.root / "latin1.txt").write_bytes("caf\xe9".encode("latin-1"))
    assert "caf�" in run(ReadFileTool(), ctx, path="latin1.txt")


def test_read_file_refusals(ws: Workspace, ctx: ToolContext) -> None:
    (ws.root / "bin.dat").write_bytes(b"\x00\x01\x02")
    (ws.root / ".env").write_text("OPENAI_API_KEY=sk-x", encoding="utf-8")
    (ws.root / "dir").mkdir()
    tool = ReadFileTool()
    with pytest.raises(ToolError, match="binary"):
        run(tool, ctx, path="bin.dat")
    with pytest.raises(WorkspaceError, match="credentials"):
        run(tool, ctx, path=".env")
    with pytest.raises(ToolError, match="directory"):
        run(tool, ctx, path="dir")
    with pytest.raises(ToolError, match="does not exist"):
        run(tool, ctx, path="missing.txt")
    with pytest.raises(ToolError, match="Invalid arguments"):
        tool.parse_args({"path": "a", "unexpected": 1})


def test_read_file_redacts_tokens(ws: Workspace, ctx: ToolContext) -> None:
    key = "sk-proj-" + "a" * 30
    (ws.root / "config.py").write_text(f'API = "{key}"\n', encoding="utf-8")
    out = run(ReadFileTool(), ctx, path="config.py")
    assert key not in out and "[REDACTED]" in out


def test_read_file_size_cap(ws: Workspace, ctx: ToolContext) -> None:
    ctx.tool_settings.max_read_bytes = 1000
    (ws.root / "big.txt").write_text("x\n" * 5000, encoding="utf-8")
    assert "truncated" in run(ReadFileTool(), ctx, path="big.txt").splitlines()[0]


# --- search_files ----------------------------------------------------------------


def test_search_literal_and_regex(ws: Workspace, ctx: ToolContext) -> None:
    (ws.root / "a.py").write_text("def Foo():\n    return 1\n", encoding="utf-8")
    (ws.root / "b.txt").write_text("foo bar\n", encoding="utf-8")
    (ws.root / ".env").write_text("FOO=secret\n", encoding="utf-8")
    (ws.root / "bin.dat").write_bytes(b"foo\x00")
    out = run(SearchFilesTool(), ctx, query="foo")
    assert out.splitlines() == ["a.py:1: def Foo():", "b.txt:1: foo bar"]
    assert run(SearchFilesTool(), ctx, query="foo", case_sensitive=True) == "b.txt:1: foo bar"
    assert run(SearchFilesTool(), ctx, query=r"return \d", regex=True) == "a.py:2: return 1"
    assert run(SearchFilesTool(), ctx, query="foo", glob="*.txt") == "b.txt:1: foo bar"
    assert run(SearchFilesTool(), ctx, query="zzz").startswith("No matches")


def test_search_rejects_bad_regex(ctx: ToolContext) -> None:
    tool = SearchFilesTool()
    with pytest.raises(ToolError, match="Invalid regular expression"):
        tool.prepare(tool.parse_args({"query": "(", "regex": True}), ctx)
    with pytest.raises(ToolError, match="nested quantifiers"):
        tool.prepare(tool.parse_args({"query": "(a+)+$", "regex": True}), ctx)


def test_search_skips_symlink_escapes(
    ws: Workspace, ctx: ToolContext, tmp_path: Path, symlinks: None
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("needle", encoding="utf-8")
    (ws.root / "link.txt").symlink_to(outside)
    assert run(SearchFilesTool(), ctx, query="needle").startswith("No matches")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_unreadable_files_are_skipped(ws: Workspace, ctx: ToolContext) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can read everything")
    locked = ws.root / "locked.txt"
    locked.write_text("needle", encoding="utf-8")
    locked.chmod(0)
    try:
        assert run(SearchFilesTool(), ctx, query="needle").startswith("No matches")
    finally:
        locked.chmod(0o600)
