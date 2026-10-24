from __future__ import annotations

import os
from pathlib import Path

import pytest

from apexcore.errors import ToolError, WorkspaceError
from apexcore.tools.base import ToolContext
from apexcore.tools.edit import EditFileTool, WriteFileTool


def prepare(tool, ctx: ToolContext, **raw):
    return tool.prepare(tool.parse_args(raw), ctx)


def test_write_new_file_shows_diff_and_writes_only_on_run(ws, ctx: ToolContext) -> None:
    action = prepare(WriteFileTool(), ctx, path="pkg/new.py", content="print('hi')\n")
    assert action.summary == "create pkg/new.py (1 lines)"
    assert "--- /dev/null" in action.details and "+print('hi')" in action.details
    assert not (ws.root / "pkg" / "new.py").exists()  # nothing happens before approval
    assert action.run().ok
    assert (ws.root / "pkg" / "new.py").read_bytes() == b"print('hi')\n"


def test_overwrite_and_no_temp_files_left(ws, ctx: ToolContext) -> None:
    target = ws.root / "a.txt"
    target.write_text("old\nline\n", encoding="utf-8")
    action = prepare(WriteFileTool(), ctx, path="a.txt", content="new\nline\n")
    assert action.summary == "overwrite a.txt (+1 -1)"
    assert "-old" in action.details and "+new" in action.details
    action.run()
    assert target.read_text(encoding="utf-8") == "new\nline\n"
    assert [p.name for p in ws.root.iterdir()] == ["a.txt"]


def test_write_refuses_unchanged_content(ws, ctx: ToolContext) -> None:
    (ws.root / "a.txt").write_text("same", encoding="utf-8")
    with pytest.raises(ToolError, match="nothing to do"):
        prepare(WriteFileTool(), ctx, path="a.txt", content="same")


def test_write_detects_concurrent_change(ws, ctx: ToolContext) -> None:
    target = ws.root / "a.txt"
    target.write_text("v1", encoding="utf-8")
    action = prepare(WriteFileTool(), ctx, path="a.txt", content="v2")
    target.write_text("changed by someone else", encoding="utf-8")
    with pytest.raises(ToolError, match="changed since the preview"):
        action.run()
    assert target.read_text(encoding="utf-8") == "changed by someone else"


@pytest.mark.parametrize("path", [".env", "keys/server.pem", ".git/hooks/pre-commit", "../x.py"])
def test_write_blocked_paths(ws, ctx: ToolContext, path: str) -> None:
    (ws.root / ".git").mkdir()
    with pytest.raises(WorkspaceError):
        prepare(WriteFileTool(), ctx, path=path, content="x")


def test_write_refuses_redaction_placeholder(ws, ctx: ToolContext) -> None:
    (ws.root / "settings.py").write_text('KEY = "sk-real"\n', encoding="utf-8")
    with pytest.raises(ToolError, match="placeholder"):
        prepare(WriteFileTool(), ctx, path="settings.py", content='KEY = "[REDACTED]"\n')


def test_write_refuses_binary_and_directories(ws, ctx: ToolContext) -> None:
    (ws.root / "img.png").write_bytes(b"\x89PNG\x00\x00")
    (ws.root / "dir").mkdir()
    with pytest.raises(ToolError, match="binary"):
        prepare(WriteFileTool(), ctx, path="img.png", content="x")
    with pytest.raises(ToolError, match="not a regular file"):
        prepare(WriteFileTool(), ctx, path="dir", content="x")
    with pytest.raises(ToolError, match="workspace root"):
        prepare(WriteFileTool(), ctx, path=".", content="x")


def test_crlf_files_keep_crlf(ws, ctx: ToolContext) -> None:
    target = ws.root / "win.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    prepare(WriteFileTool(), ctx, path="win.txt", content="one\nTWO\n").run()
    assert target.read_bytes() == b"one\r\nTWO\r\n"
    prepare(EditFileTool(), ctx, path="win.txt", old_text="one\nTWO", new_text="1\n2").run()
    assert target.read_bytes() == b"1\r\n2\r\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_new_files_follow_umask(ws, ctx: ToolContext) -> None:
    old_umask = os.umask(0o022)
    try:
        prepare(WriteFileTool(), ctx, path="new.txt", content="x").run()
    finally:
        os.umask(old_umask)
    assert (ws.root / "new.txt").stat().st_mode & 0o777 == 0o644


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_file_mode_is_preserved(ws, ctx: ToolContext) -> None:
    script = ws.root / "run.sh"
    script.write_text("echo 1\n", encoding="utf-8")
    script.chmod(0o755)
    prepare(EditFileTool(), ctx, path="run.sh", old_text="1", new_text="2").run()
    assert script.stat().st_mode & 0o777 == 0o755


def test_edit_file(ws, ctx: ToolContext) -> None:
    target = ws.root / "m.py"
    target.write_text("a = 1\nb = 1\n", encoding="utf-8")
    action = prepare(EditFileTool(), ctx, path="m.py", old_text="a = 1", new_text="a = 2")
    assert action.summary == "edit m.py (+1 -1)"
    action.run()
    assert target.read_text(encoding="utf-8") == "a = 2\nb = 1\n"


def test_edit_file_errors(ws, ctx: ToolContext) -> None:
    (ws.root / "m.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    with pytest.raises(ToolError, match="appears 2 times"):
        prepare(EditFileTool(), ctx, path="m.py", old_text="x = 1", new_text="x = 2")
    with pytest.raises(ToolError, match="not found"):
        prepare(EditFileTool(), ctx, path="m.py", old_text="y = 1", new_text="y = 2")
    with pytest.raises(ToolError, match="does not exist"):
        prepare(EditFileTool(), ctx, path="nope.py", old_text="a", new_text="b")
    with pytest.raises(ToolError, match="placeholder"):
        prepare(EditFileTool(), ctx, path="m.py", old_text="[REDACTED]", new_text="b")
    action = prepare(
        EditFileTool(), ctx, path="m.py", old_text="x = 1", new_text="x = 2", replace_all=True
    )
    assert "2 occurrences" in action.summary
    action.run()
    assert (ws.root / "m.py").read_text(encoding="utf-8") == "x = 2\nx = 2\n"


def test_edit_refuses_non_utf8(ws, ctx: ToolContext) -> None:
    (ws.root / "l1.txt").write_bytes("caf\xe9".encode("latin-1"))
    with pytest.raises(ToolError, match="UTF-8"):
        prepare(EditFileTool(), ctx, path="l1.txt", old_text="caf", new_text="tea")


def test_symlink_swapped_after_preview_is_refused(
    ws, ctx: ToolContext, tmp_path: Path, symlinks: None
) -> None:
    target = ws.root / "a.txt"
    target.write_text("v1", encoding="utf-8")
    action = prepare(WriteFileTool(), ctx, path="a.txt", content="v2")
    outside = tmp_path / "outside.txt"
    outside.write_text("v1", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(ToolError):
        action.run()
    assert outside.read_text(encoding="utf-8") == "v1"
