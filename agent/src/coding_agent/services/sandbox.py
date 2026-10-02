"""Bubblewrap sandbox for run_command.

Commands see the whole filesystem read-only, can write only the workspace, /tmp (private) and ~/.cache, have no
network unless SANDBOX_NETWORK=true, and cannot see well-known credential locations or the agent's own state.
This contains approved commands; the approval policy still decides what runs at all.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from coding_agent.config.settings import AGENT_ENV_FILE, Settings

# Directories replaced by an empty tmpfs inside the sandbox.
HIDDEN_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud", ".config/gh", ".docker", ".kube", ".password-store")
# Files replaced by /dev/null inside the sandbox.
HIDDEN_FILES = (".netrc", ".git-credentials", ".pgpass", ".pypirc", ".npmrc")


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class SandboxStatus:
    enabled: bool
    network: bool
    reason: str = ""

    def describe(self) -> str:
        if not self.enabled:
            return f"off{f' ({self.reason})' if self.reason else ''}"
        return "bwrap" + ("" if self.network else " (no network)")


def in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def sandbox_status(settings: Settings) -> SandboxStatus:
    mode = settings.sandbox
    if mode == "off":
        return SandboxStatus(False, True, "disabled by SANDBOX=off")
    available = shutil.which("bwrap") is not None
    if mode == "bwrap":
        if not available:
            raise SandboxError("SANDBOX=bwrap but bubblewrap (bwrap) is not installed")
        return SandboxStatus(True, settings.sandbox_network)
    # auto
    if not available:
        return SandboxStatus(False, True, "bubblewrap not installed")
    if in_container():
        return SandboxStatus(False, True, "already running in a container")
    return SandboxStatus(True, settings.sandbox_network)


def sandbox_argv(settings: Settings, workspace: Path, cwd: Path, home: Path | None = None) -> list[str] | None:
    """bwrap argument prefix for running a command in `cwd`, or None when sandboxing is off."""
    status = sandbox_status(settings)
    if not status.enabled:
        return None
    home = home or Path.home()
    mounts = [
        ("--ro-bind", "/", "/"),
        ("--dev", "/dev"),
        ("--proc", "/proc"),
        ("--tmpfs", "/tmp"),
        ("--bind", str(workspace), str(workspace)),
    ]
    argv = ["bwrap", *(arg for mount in mounts for arg in mount)]
    cache = home / ".cache"
    if cache.is_dir():
        argv += ["--bind", str(cache), str(cache)]
    hidden_dirs = [home / d for d in HIDDEN_DIRS] + [settings.state_dir]
    for directory in hidden_dirs:
        # The workspace itself must stay visible even if it lives under a hidden path.
        if directory.is_dir() and not workspace.is_relative_to(directory):
            argv += ["--tmpfs", str(directory)]
    hidden_files = [home / f for f in HIDDEN_FILES] + [Path(os.environ.get("AGENT_ENV_FILE") or AGENT_ENV_FILE)]
    for file in hidden_files:
        if file.is_file():
            argv += ["--ro-bind", "/dev/null", str(file)]
    argv += ["--unshare-all"]
    if status.network:
        argv += ["--share-net"]
    argv += ["--die-with-parent", "--new-session", "--chdir", str(cwd)]
    return argv
