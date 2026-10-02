"""The workspace is the only part of the filesystem the agent may touch."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coding_agent.utils.security import SecurityError, is_secret_path, relative_display, resolve_in_workspace

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".coding-agent",
}


@dataclass(frozen=True)
class Workspace:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve())
        if not self.root.is_dir():
            raise ValueError(f"Workspace {self.root} is not a directory")

    def resolve(self, path: str | Path) -> Path:
        return resolve_in_workspace(self.root, path)

    def resolve_readable(self, path: str | Path) -> Path:
        resolved = self.resolve(path)
        if is_secret_path(resolved):
            raise SecurityError(
                f"{self.display(resolved)} looks like a secrets file; reading it is blocked. "
                "Ask the user to provide the specific non-secret value you need."
            )
        return resolved

    @staticmethod
    def is_secret(path: Path) -> bool:
        return is_secret_path(path)

    def display(self, path: Path) -> str:
        return relative_display(self.root, path)

    @staticmethod
    def is_ignored_dir(name: str) -> bool:
        return name in IGNORED_DIRS
