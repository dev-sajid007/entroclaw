"""Shell tool: run development commands inside the workspace with timeouts and output limits."""

from __future__ import annotations

import asyncio
import os
import signal
import time

from langchain_core.tools import BaseTool, tool

from coding_agent.config.settings import Settings
from coding_agent.services.workspace import Workspace
from coding_agent.tools.filesystem import ToolError
from coding_agent.utils.security import filtered_env, truncate

MAX_TIMEOUT = 600


def _stream_writer():
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # not running inside a graph
        return lambda _chunk: None


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
    except ProcessLookupError:
        pass


async def execute_command(
    command: str,
    cwd: str,
    timeout: int,
    max_output: int,
    on_output=None,
) -> tuple[int | None, str, bool, float]:
    """Run `command` through bash. Returns (exit_code, output, timed_out, seconds)."""
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        command,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=filtered_env(),
        start_new_session=True,
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
        await _kill_group(proc)
    return proc.returncode, truncate("".join(chunks), max_output), timed_out, time.monotonic() - started


def make_shell_tools(settings: Settings, workspace: Workspace) -> list[BaseTool]:
    @tool
    async def run_command(command: str, cwd: str = ".", timeout: int | None = None) -> str:
        """Run a shell command (bash) in the workspace, e.g. tests, linters, builds.

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

        code, output, timed_out, seconds = await execute_command(command, str(directory), limit, settings.max_tool_output, on_output)
        status = f"timed out after {limit}s (process killed)" if timed_out else f"exit code {code}"
        return f"$ {command}\n[{status}, {seconds:.1f}s]\n{output or '(no output)'}"

    return [run_command]
