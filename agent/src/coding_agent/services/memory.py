"""Project instructions (from the repository) and long-term memory (owned by the agent).

Instructions are read from the first of INSTRUCTION_FILES at the workspace root on every model call, so edits
apply immediately. Memory notes live in the agent's state directory, never in the repository:
`memory/global.md` applies everywhere, `memory/<workspace hash>.md` to one workspace.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from coding_agent.services.workspace import Workspace
from coding_agent.utils.logging import SECRET_VALUE
from coding_agent.utils.security import SecurityError, truncate

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".entroclaw/instructions.md", ".coding-agent/instructions.md")
MAX_INSTRUCTIONS_CHARS = 20_000
MAX_MEMORY_CHARS = 8_000
MAX_NOTE_CHARS = 500

Scope = Literal["project", "global"]
SCOPES: tuple[Scope, ...] = ("project", "global")


class MemoryError(Exception):
    pass


def load_instructions(workspace: Workspace) -> tuple[str | None, str]:
    """Return (relative source path, text) for the first instructions file found, or (None, "")."""
    for name in INSTRUCTION_FILES:
        try:
            path = workspace.resolve(name)
        except SecurityError:
            continue
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            return name, truncate(text, MAX_INSTRUCTIONS_CHARS)
    return None, ""


@dataclass
class MemoryStore:
    state_dir: Path
    workspace: Path

    def path(self, scope: Scope) -> Path:
        if scope == "global":
            return self.state_dir / "memory" / "global.md"
        digest = hashlib.sha1(str(self.workspace.resolve()).encode()).hexdigest()[:16]
        return self.state_dir / "memory" / f"{digest}.md"

    def read(self, scope: Scope) -> str:
        path = self.path(scope)
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def add(self, scope: Scope, note: str) -> str:
        note = " ".join(note.split())
        if not note:
            raise MemoryError("The note is empty.")
        if len(note) > MAX_NOTE_CHARS:
            raise MemoryError(f"Keep notes under {MAX_NOTE_CHARS} characters.")
        if SECRET_VALUE.search(note):
            raise MemoryError("The note looks like it contains a secret; secrets must never be stored in memory.")
        current = self.read(scope)
        if f"- {note}" in current.splitlines():
            return f"Already remembered ({scope})."
        updated = f"{current}\n- {note}".strip() + "\n"
        if len(updated) > MAX_MEMORY_CHARS:
            raise MemoryError(f"{scope} memory is full ({MAX_MEMORY_CHARS} characters). Ask the user to clear it with /forget.")
        path = self.path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
        if scope == "project":
            # Record which workspace the hashed file belongs to, for humans inspecting the state dir.
            path.with_suffix(".workspace").write_text(str(self.workspace.resolve()), encoding="utf-8")
        return f"Remembered ({scope}): {note}"

    def clear(self, scope: Scope) -> None:
        self.path(scope).unlink(missing_ok=True)
