"""Shell tool: run development commands inside the workspace with timeouts and output limits."""

from __future__ import annotations

import asyncio
import time

from langchain_core.tools import BaseTool, tool

from coding_agent.config.settings import Settings
from coding_agent.services.sandbox import sandbox_argv
from coding_agent.services.shell_env import kill_process_tree, process_group_kwargs, resolve_shell
from coding_agent.services.workspace import Workspace
from coding_agent.tools.filesystem import ToolError
from coding_agent.utils.security import filtered_env, truncate

MAX_TIMEOUT = 600
SANDBOX_HINTS = (
    "Read-only file system",
    "Permission denied",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "Could not resolve host",
    "Name or service not known",
)
SANDBOX_HINT = (
    "\n[sandbox] This command ran in a sandbox: only the workspace, /tmp and ~/.cache are writable and there is "
    "no network access. If the failure is caused by that, tell the user; they can set SANDBOX_NETWORK=true or SANDBOX=off."
)


def _stream_writer():
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # not running inside a graph
        return lambda _chunk: None


async def execute_command(
    command: str,
    cwd: str,
    timeout: int,
    max_output: int,
    on_output=None,
    argv_prefix: list[str] | None = None,
) -> tuple[int | None, str, bool, float]:
    """Run `command` in the platform shell (inside `argv_prefix`, e.g. a sandbox).

    Returns (exit_code, output, timed_out, seconds)."""
    started = time.monotonic()
    shell = resolve_shell()
    # Inside the Linux sandbox the host's bash path is still valid (the root is bind-mounted read-only).
    proc = await asyncio.create_subprocess_exec(
        *(argv_prefix or []),
        *shell.command(command),
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=filtered_env(),
        **process_group_kwargs(),
    )
    chunks: list[str] = []
    size = 0
    cap = max_output * 4  # keep a bounded buffer; truncate() trims the middle afterwards

    async def pump() -> None:
        nonlocal size
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            text = line.decode("utf-8", errors="replace")
            if on_output:
                on_output(text)
            if size < cap:
                chunks.append(text)
                size += len(text)

    timed_out = False
    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
    except TimeoutError:
        timed_out = True
    finally:
        await kill_process_tree(proc)
    return proc.returncode, truncate("".join(chunks), max_output), timed_out, time.monotonic() - started


def make_shell_tools(settings: Settings, workspace: Workspace) -> list[BaseTool]:
    @tool
    async def run_command(command: str, cwd: str = ".", timeout: int | None = None) -> str:
        """Run a shell command in the workspace, e.g. tests, linters, builds (bash; PowerShell on Windows without Git Bash).

        `cwd` is relative to the workspace. Commands are non-interactive (no stdin), time out after
        `timeout` seconds and have their output truncated. Output streams to the user as it runs.
        """
        directory = workspace.resolve(cwd)
        if not directory.is_dir():
            raise ToolError(f"cwd {cwd!r} is not a directory")
        limit = min(max(timeout or settings.command_timeout, 1), MAX_TIMEOUT)
        writer = _stream_writer()

        def on_output(text: str) -> None:
            writer({"type": "tool_output", "tool": "run_command", "content": text})

        prefix = sandbox_argv(settings, workspace.root, directory)
        code, output, timed_out, seconds = await execute_command(
            command, str(directory), limit, settings.max_tool_output, on_output, argv_prefix=prefix
        )
        status = f"timed out after {limit}s (process killed)" if timed_out else f"exit code {code}"
        sandboxed = ", sandboxed" if prefix else ""
        result = f"$ {command}\n[{status}, {seconds:.1f}s{sandboxed}]\n{output or '(no output)'}"
        if prefix and code not in (0, None) and any(hint in output for hint in SANDBOX_HINTS):
            result += SANDBOX_HINT
        return result

    return [run_command]
