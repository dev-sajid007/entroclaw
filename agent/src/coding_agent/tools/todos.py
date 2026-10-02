"""The `update_todos` tool: the agent's live checklist. Executed by the tools node, which stores it in graph state."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel

MAX_TODOS = 30
MAX_TODO_CHARS = 200
STATUSES = ("pending", "in_progress", "completed")


class TodoError(Exception):
    pass


class TodoItem(BaseModel):
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


def validate_todos(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise TodoError("todos must be a list of {content, status} items")
    if len(raw) > MAX_TODOS:
        raise TodoError(f"keep the list to at most {MAX_TODOS} items")
    todos = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("content", "")).strip():
            raise TodoError("each todo needs non-empty content")
        status = item.get("status", "pending")
        if status not in STATUSES:
            raise TodoError(f"status must be one of {', '.join(STATUSES)}")
        todos.append({"content": " ".join(str(item["content"]).split())[:MAX_TODO_CHARS], "status": status})
    if sum(t["status"] == "in_progress" for t in todos) > 1:
        raise TodoError("only one item can be in_progress at a time")
    return todos


def make_todo_tools() -> list[BaseTool]:
    @tool
    def update_todos(todos: list[TodoItem]) -> str:
        """Replace the task checklist shown to the user. Send the complete list every time.

        Each item has `content` and `status` (pending | in_progress | completed); at most one in_progress.
        """
        raise RuntimeError("update_todos is executed by the agent runtime")  # pragma: no cover

    return [update_todos]
