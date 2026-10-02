"""Git tools. Read operations are free; write operations go through the approval policy.

High-risk operations (reset, clean, force push, history rewriting) are deliberately not exposed as
tools; if the model attempts them through run_command the policy flags them as high risk.
"""

from __future__ import annotations

import asyncio

from langchain_core.tools import BaseTool, tool

from coding_agent.config.settings import Settings
from coding_agent.services.workspace import Workspace
from coding_agent.tools.filesystem import ToolError
from coding_agent.utils.security import filtered_env, truncate

GIT_TIMEOUT = 30


async def run_git(workspace: Workspace, args: list[str], max_output: int) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "--no-pager",
        *args,
        cwd=str(workspace.root),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**filtered_env(), "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolError(f"git {args[0]} timed out") from None
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise ToolError(f"git {' '.join(args)} failed (exit {proc.returncode}):\n{(err or out).strip()}")
    return truncate((out + err).strip() or "(no output)", max_output)


def _paths(workspace: Workspace, paths: list[str]) -> list[str]:
    return [workspace.display(workspace.resolve(p)) for p in paths]


def make_git_tools(settings: Settings, workspace: Workspace) -> list[BaseTool]:
    limit = settings.max_tool_output

    @tool
    async def git_status() -> str:
        """Show the working tree status (branch, staged, unstaged and untracked files)."""
        return await run_git(workspace, ["status", "--short", "--branch"], limit)

    @tool
    async def git_diff(staged: bool = False, paths: list[str] | None = None) -> str:
        """Show changes. `staged=True` shows what is staged for commit. Optionally limit to `paths`."""
        args = ["diff", "--stat", "--patch"]
        if staged:
            args.append("--cached")
        if paths:
            args += ["--", *_paths(workspace, paths)]
        return await run_git(workspace, args, limit)

    @tool
    async def git_log(max_count: int = 10, path: str | None = None) -> str:
        """Show recent commits (one line each), optionally only those touching `path`."""
        args = ["log", f"--max-count={min(max(max_count, 1), 100)}", "--oneline", "--decorate"]
        if path:
            args += ["--", *_paths(workspace, [path])]
        return await run_git(workspace, args, limit)

    @tool
    async def git_show(revision: str = "HEAD") -> str:
        """Show a commit's message and patch."""
        if revision.startswith("-"):
            raise ToolError("revision must not start with '-'")
        return await run_git(workspace, ["show", "--stat", "--patch", revision], limit)

    @tool
    async def git_branch(create: str | None = None) -> str:
        """List branches, or create and switch to a new branch named `create`."""
        if create is None:
            return await run_git(workspace, ["branch", "--list", "-vv"], limit)
        if create.startswith("-"):
            raise ToolError("branch name must not start with '-'")
        return await run_git(workspace, ["switch", "-c", create], limit)

    @tool
    async def git_add(paths: list[str]) -> str:
        """Stage the given files for commit."""
        if not paths:
            raise ToolError("Provide at least one path to stage")
        await run_git(workspace, ["add", "--", *_paths(workspace, paths)], limit)
        return await run_git(workspace, ["status", "--short"], limit)

    @tool
    async def git_commit(message: str) -> str:
        """Commit the staged changes with `message`."""
        if not message.strip():
            raise ToolError("Commit message must not be empty")
        return await run_git(workspace, ["commit", "-m", message], limit)

    return [git_status, git_diff, git_log, git_show, git_branch, git_add, git_commit]
