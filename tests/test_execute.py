from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from apexcore.errors import CommandBlockedError, ToolError, WorkspaceError
from apexcore.platform_utils import IS_WINDOWS, which_no_cwd
from apexcore.tools.base import ToolContext
from apexcore.tools.execute import RunCommandTool, assess_command
from apexcore.tools.git import GitDiffTool, GitStatusTool
from apexcore.tools.process import run_process, scrubbed_env

PY = sys.executable


def prepare(ctx: ToolContext, argv: list[str], **extra):
    tool = RunCommandTool()
    return tool.prepare(tool.parse_args({"argv": argv, **extra}), ctx)


# --- Classification (pure, runs everywhere) -----------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["sudo", "ls"],
        ["SUDO.EXE"],
        ["mkfs.ext4", "/dev/sda"],
        ["dd", "if=x"],
        ["shutdown", "/s"],
        ["diskpart"],
        ["/usr/bin/doas", "sh"],
        ["runas", "/user:admin", "cmd"],
    ],
)
def test_blocked_commands(argv: list[str]) -> None:
    assert assess_command(argv).blocked_reason


@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", "build"],
        ["del", "x"],
        ["git", "reset", "--hard"],
        ["git", "clean", "-fdx"],
        ["git", "push", "--force"],
        ["git", "-C", "repo", "clean", "-f"],
        ["git", "branch", "-D", "x"],
        ["bash", "-c", "echo hi"],
        ["powershell", "-Command", "ls"],
        ["cmd", "/c", "dir"],
        [PY, "-c", "print(1)"],
        ["node", "-e", "1"],
        ["find", ".", "-delete"],
        ["curl", "http://x"],
        ["make", "rm -rf /"],
    ],
)
def test_destructive_or_opaque_commands_need_explicit_approval(argv: list[str]) -> None:
    result = assess_command(argv)
    assert result.blocked_reason is None
    assert result.auto_approvable is False and result.warnings


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "status"],
        ["git", "diff"],
        ["git", "checkout", "main"],
        ["python", "-m", "pytest"],
        ["npm", "test"],
        ["ruff", "check", "."],
        ["git", "commit", "-m", "rm stuff"],
    ],
)
def test_ordinary_commands(argv: list[str]) -> None:
    result = assess_command(argv)
    assert result.blocked_reason is None and result.auto_approvable


# --- Executable resolution ------------------------------------------------------


def test_which_ignores_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = "apex-fake-tool"
    fake = tmp_path / (name + (".exe" if IS_WINDOWS else ""))
    fake.write_bytes(b"")
    fake.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    assert which_no_cwd(name, path_env=os.pathsep.join([".", "", "relative/dir"])) is None
    assert which_no_cwd(name, path_env=str(tmp_path)) == fake


def test_python_resolves_from_path() -> None:
    assert which_no_cwd(Path(PY).name, path_env=str(Path(PY).parent)) is not None


# --- run_command --------------------------------------------------------------------


def test_refused_when_elevated(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apexcore.tools.execute.is_elevated", lambda: True)
    with pytest.raises(CommandBlockedError, match="root/Administrator"):
        prepare(ctx, [PY, "--version"])
    ctx.permission_settings.allow_elevated = True
    assert prepare(ctx, [PY, "--version"]).argv == [PY, "--version"]


def test_run_command_success(ctx: ToolContext, not_elevated: None) -> None:
    action = prepare(ctx, [PY, "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert action.auto_approvable is False  # inline code is never auto-approved
    result = action.run()
    assert result.ok
    assert "exit code: 0" in result.content
    assert "--- stdout ---\nout" in result.content and "--- stderr ---\nerr" in result.content


def test_run_command_failure_exit_code(ctx: ToolContext, not_elevated: None) -> None:
    result = prepare(ctx, [PY, "-c", "raise SystemExit(3)"]).run()
    assert not result.ok and "exit code: 3" in result.content


def test_run_command_timeout_kills_process(ctx: ToolContext, not_elevated: None) -> None:
    started = time.monotonic()
    result = prepare(ctx, [PY, "-c", "import time; time.sleep(60)"], timeout=1).run()
    assert time.monotonic() - started < 20
    assert not result.ok and "TIMED OUT" in result.content


def test_run_command_output_is_capped(ctx: ToolContext, not_elevated: None) -> None:
    ctx.tool_settings.max_output_chars = 2000
    result = prepare(ctx, [PY, "-c", "print('x' * 1_000_000)"]).run()
    assert len(result.content) < 5000
    assert "truncated" in result.content or "omitted" in result.content


def test_stdin_is_closed(ctx: ToolContext, not_elevated: None) -> None:
    started = time.monotonic()
    result = prepare(ctx, [PY, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=20).run()
    assert "''" in result.content and time.monotonic() - started < 15


def test_secrets_are_scrubbed_from_child_env(
    ctx: ToolContext, not_elevated: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak-123456")
    monkeypatch.setenv("CUSTOM_PROVIDER_VAR", "also-hidden-123456")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Kept Name")
    ctx.secret_env_names = frozenset({"CUSTOM_PROVIDER_VAR"})
    code = "import os; print(sorted(k for k in os.environ if k in "
    code += "('OPENAI_API_KEY','CUSTOM_PROVIDER_VAR','GIT_AUTHOR_NAME')))"
    result = prepare(ctx, [PY, "-c", code]).run()
    assert "['GIT_AUTHOR_NAME']" in result.content


def test_scrubbed_env_patterns() -> None:
    env = scrubbed_env(
        {
            "PATH": "/bin",
            "AWS_SECRET_ACCESS_KEY": "x",
            "GITHUB_TOKEN": "y",
            "HOME": "/h",
            "db_password": "z",
            "MY_VAR": "v",
        },
        extra_names=["my_var"],
    )
    assert env == {"PATH": "/bin", "HOME": "/h"}


def test_cwd_must_stay_inside_workspace(ctx: ToolContext, not_elevated: None) -> None:
    with pytest.raises(WorkspaceError):
        prepare(ctx, [PY, "--version"], cwd="..")
    (ctx.workspace.root / "sub").mkdir()
    result = prepare(ctx, [PY, "-c", "import os; print(os.getcwd())"], cwd="sub").run()
    assert "sub" in result.content


def test_relative_executable_must_be_inside_workspace(ctx: ToolContext, not_elevated: None) -> None:
    with pytest.raises(CommandBlockedError):
        prepare(ctx, ["../evil"])
    with pytest.raises(ToolError, match="not found"):
        prepare(ctx, ["./missing-script"])


def test_unknown_program(ctx: ToolContext, not_elevated: None) -> None:
    with pytest.raises(ToolError, match="not found on PATH"):
        prepare(ctx, ["definitely-not-a-real-program-xyz"])


def test_blocked_program_is_refused(ctx: ToolContext, not_elevated: None) -> None:
    with pytest.raises(CommandBlockedError, match="blocked"):
        prepare(ctx, ["sudo", "true"])


@pytest.mark.skipif(not IS_WINDOWS, reason="batch-file quoting is a Windows concern")
def test_batch_file_metacharacters_refused(ctx: ToolContext, not_elevated: None) -> None:
    script = ctx.workspace.root / "build.cmd"
    script.write_text("@echo %1\r\n", encoding="utf-8")
    with pytest.raises(CommandBlockedError, match="batch file"):
        prepare(ctx, ["./build.cmd", "a&calc"])
    result = prepare(ctx, ["./build.cmd", "hello"]).run()
    assert "hello" in result.content


def test_keyboard_interrupt_kills_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "pid.txt"
    code = f"import os, time; open({str(marker)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    real_sleep = time.sleep
    state = {"raised": False}

    def interrupt_once(seconds: float) -> None:
        # Simulate Ctrl+C once the child is running; later sleeps (inside the
        # cleanup) behave normally.
        if marker.exists() and not state["raised"]:
            state["raised"] = True
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr("apexcore.tools.process.time.sleep", interrupt_once)
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_process([PY, "-c", code], cwd=tmp_path, timeout=60, max_output_bytes=1000)
    monkeypatch.setattr("apexcore.tools.process.time.sleep", real_sleep)
    assert time.monotonic() - started < 30
    pid = int(marker.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if IS_WINDOWS:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still "exists" until reaped by its parent; treat it as dead.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().split()[2] != "Z"
    except OSError:
        return True


# --- git tools ------------------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@needs_git
def test_git_status_and_diff(ctx: ToolContext) -> None:
    root = ctx.workspace.root
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=one\n", encoding="utf-8")
    _git(root, "add", "-f", "a.txt", ".env")
    _git(root, "commit", "-q", "-m", "init")
    (root / "a.txt").write_text("two\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=two\n", encoding="utf-8")
    (root / "new.txt").write_text("n\n", encoding="utf-8")
    status = GitStatusTool().prepare(GitStatusTool().parse_args({}), ctx).run().content
    assert " M a.txt" in status and "?? new.txt" in status
    diff = GitDiffTool().prepare(GitDiffTool().parse_args({}), ctx).run().content
    assert "+two" in diff and "TOKEN" not in diff  # credentials files are excluded


@needs_git
def test_git_outside_repo(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(ctx.workspace.root.parent))
    with pytest.raises(ToolError, match="not inside a git repository"):
        GitStatusTool().prepare(GitStatusTool().parse_args({}), ctx).run()


@needs_git
def test_git_status_does_not_run_repo_fsmonitor(ctx: ToolContext) -> None:
    root = ctx.workspace.root
    _git(root, "init", "-q")
    (root / "a.txt").write_text("x", encoding="utf-8")
    marker = root.parent / "fsmonitor-ran.txt"
    hook = root.parent / ("hook.py")
    hook.write_text(f"open({str(marker)!r}, 'w').write('pwned')\n", encoding="utf-8")
    # git runs the hook through sh (Git for Windows ships one), so use forward slashes.
    command = f"'{Path(PY).as_posix()}' '{hook.as_posix()}'"
    _git(root, "config", "core.fsmonitor", command)
    # Control: a plain `git status` honours the repository's fsmonitor hook...
    subprocess.run(["git", "status"], cwd=root, capture_output=True, check=False)
    if not marker.exists():
        pytest.skip("this git version did not invoke core.fsmonitor")
    marker.unlink()
    # ...but APEX CORE's invocation must not.
    GitStatusTool().prepare(GitStatusTool().parse_args({}), ctx).run()
    assert not marker.exists()
