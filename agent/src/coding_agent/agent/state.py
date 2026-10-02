from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ApprovalDecision(TypedDict, total=False):
    approved: bool
    feedback: str
    by_policy: bool
    always: bool


class Todo(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed"]


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    # Approval outcome per tool_call_id for the most recent batch of tool calls.
    decisions: dict[str, ApprovalDecision]
    # Model calls made for the current user turn; bounded by Settings.max_iterations.
    iterations: int
    # Summary of older messages that were removed to stay within the context budget.
    summary: str
    # Session "always allow" rules (ApprovalPolicy.rule_key values) for sensitive, non-high-risk actions.
    allow_rules: list[str]
    # Per-session model override ("provider:model"); the default model when unset.
    model: str
    # "plan" restricts the agent to read-only tools until the user approves; default "build".
    mode: Literal["build", "plan"]
    # The agent's live checklist, maintained with the update_todos tool.
    todos: list[Todo]
