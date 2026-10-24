"""Read-only git tools: git_status and git_diff.

These run a fixed argv chosen by APEX CORE (never by the model), so they need
no approval. Repository configuration can make git run commands, so the
invocation overrides the known hooks for the operations used here:

* ``core.fsmonitor`` (executed by ``git status``) is forced off;
* external diff drivers and textconv filters are disabled for ``git diff``;
* ``GIT_OPTIONAL_LOCKS=0`` stops ``git status`` from writing the index.

A ``.git/config`` crafted by an attacker (for example inside a downloaded
archive rather than a fresh clone) can still define clean/smudge filters,
exactly as it could if you ran ``git status`` yourself. See SECURITY.md.
"""

from __future__ import annotations

from pydantic import Field

from apexcore.errors import ToolError
from apexcore.platform_utils import which_no_cwd
from apexcore.tools.base import (
    PreparedAction,
    Risk,
    Tool,
    ToolArgs,
    ToolContext,
    ToolResult,
    truncate,
)
from apexcore.tools.process import run_process, scrubbed_env
from apexcore.tools.workspace import DEFAULT_SECRET_PATTERNS

GIT_TIMEOUT = 30.0
GIT_MAX_OUTPUT = 400_000
SAFE_GIT_OPTIONS = [
    "--no-pager",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "color.ui=false",
]


def run_git(args: list[str], ctx: ToolContext) -> str:
    git = which_no_cwd("git")
    if git is None:
        raise ToolError("git is not installed or not on PATH.")
    env = scrubbed_env(extra_names=ctx.secret_env_names)
    env.pop("GIT_EXTERNAL_DIFF", None)
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"})
    result = run_process(
        [str(git), *SAFE_GIT_OPTIONS, *args],
        cwd=ctx.workspace.root,
        timeout=GIT_TIMEOUT,
        max_output_bytes=GIT_MAX_OUTPUT,
        env=env,
    )
    if result.timed_out:
        raise ToolError(f"git {args[0]} timed out after {GIT_TIMEOUT:.0f}s.")
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "not a git repository" in message.lower():
            raise ToolError("The workspace is not inside a git repository.")
        raise ToolError(f"git {args[0]} failed: {ctx.redactor.redact(message)[:1000]}")
    output = result.stdout
    if result.stdout_truncated:
        output += "\n... (output truncated)"
    return output


def _secret_excludes() -> list[str]:
    return [f":(exclude,glob,icase)**/{pattern}" for pattern in DEFAULT_SECRET_PATTERNS]


class GitStatusArgs(ToolArgs):
    pass


class GitStatusTool(Tool):
    name = "git_status"
    description = (
        "Show the git branch and changed/untracked files (git status --porcelain=v1 --branch)."
    )
    risk = Risk.READ
    Args = GitStatusArgs

    def prepare(self, args: GitStatusArgs, ctx: ToolContext) -> PreparedAction:
        def run() -> ToolResult:
            output = run_git(
                ["status", "--porcelain=v1", "--branch", "--untracked-files=normal"], ctx
            )
            return ToolResult(output.strip() or "(clean working tree)")

        return PreparedAction(summary="git status", run=run)


class GitDiffArgs(ToolArgs):
    staged: bool = Field(default=False, description="Show staged changes (git diff --cached).")
    path: str = Field(default="", description="Limit the diff to this path (optional).")


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show uncommitted changes as a unified diff. Credentials files are excluded."
    risk = Risk.READ
    Args = GitDiffArgs

    def prepare(self, args: GitDiffArgs, ctx: ToolContext) -> PreparedAction:
        ws = ctx.workspace
        pathspec = ws.relative(ws.resolve(args.path)) if args.path else "."

        def run() -> ToolResult:
            cmd = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
            if args.staged:
                cmd.append("--cached")
            output = run_git([*cmd, "--", pathspec, *_secret_excludes()], ctx)
            if not output.strip():
                return ToolResult("(no differences)")
            limit = ctx.tool_settings.max_output_chars
            return ToolResult(truncate(ctx.redactor.redact(output), limit, what="diff"))

        summary = "git diff" + (" --cached" if args.staged else "")
        if args.path:
            summary += f" {pathspec}"
        return PreparedAction(summary=summary, run=run)
