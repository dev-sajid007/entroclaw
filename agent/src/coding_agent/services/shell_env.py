"""Platform differences for running shell commands: which shell, and how to stop a process tree."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class Shell:
    name: str  # "bash" | "sh" | "powershell"
    argv: tuple[str, ...]  # prefix; the command string is appended

    def command(self, command: str) -> list[str]:
        return [*self.argv, command]


def _git_bash() -> str | None:
    """Git for Windows' bash. System32\\bash.exe is WSL and runs in a different filesystem, so it's never used."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        exec_path = subprocess.run([git, "--exec-path"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    # <git>/mingw64/libexec/git-core -> <git>/bin/bash.exe
    for candidate in (Path(exec_path).parents[2] / "bin" / "bash.exe", Path(git).parent.parent / "bin" / "bash.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


@cache
def resolve_shell(platform: str = sys.platform) -> Shell:
    if platform == "win32":
        bash = _git_bash()
        if bash:
            return Shell("bash", (bash, "-c"))
        powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return Shell("powershell", (powershell, "-NoProfile", "-NonInteractive", "-Command"))
    bash = shutil.which("bash")
    if bash:
        return Shell("bash", (bash, "-c"))
    return Shell("sh", (shutil.which("sh") or "/bin/sh", "-c"))


def process_group_kwargs() -> dict:
    """subprocess kwargs that put the child in its own process group, so the whole tree can be stopped."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "nt":
        # taskkill /T stops the children too; there is no process-group signal on Windows.
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/T", "/F", "/PID", str(proc.pid), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await killer.wait()
        await proc.wait()
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
