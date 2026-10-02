"""The `remember` tool: persist durable preferences and conventions across sessions."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool, tool

from coding_agent.services.memory import MemoryError, MemoryStore
from coding_agent.tools.filesystem import ToolError


def make_memory_tools(store: MemoryStore) -> list[BaseTool]:
    @tool
    def remember(note: str, scope: Literal["project", "global"] = "project") -> str:
        """Save a short, durable note for future sessions: a user preference, a repository convention, or a decision.

        `project` notes apply to this workspace only; `global` notes to every project. Do not store secrets,
        temporary task state, or anything already written in the repository's instructions file.
        """
        try:
            return store.add(scope, note)
        except MemoryError as exc:
            raise ToolError(str(exc)) from exc

    return [remember]
