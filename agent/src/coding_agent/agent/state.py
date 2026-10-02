from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ApprovalDecision(TypedDict, total=False):
    approved: bool
    feedback: str
    by_policy: bool
    always: bool


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
