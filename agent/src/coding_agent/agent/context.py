"""Context management: keep the conversation sent to the model under a token budget by summarizing old turns."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from coding_agent.utils.security import truncate

SUMMARY_PROMPT = """\
You compress the earlier part of a coding session between a user and a coding agent so the agent can continue
without the full history. Write a concise summary (at most ~600 words) that preserves:
- the user's goals and requests, and any preferences or constraints they stated
- decisions made and why
- files inspected or changed, with what changed
- commands run and their important results (test failures, errors)
- open problems and next steps
Merge in the previous summary if one is given. Output only the summary."""

TRANSCRIPT_CHARS = 120_000
TOOL_RESULT_CHARS = 1_500


def estimate_tokens(messages: list[AnyMessage]) -> int:
    return count_tokens_approximately(messages)


def find_cut(messages: list[AnyMessage], keep_tokens: int) -> int:
    """Index where the kept tail starts; everything before it may be summarized. 0 means nothing to cut.

    Cuts only at the start of a user turn or of an agent step, never between a tool call and its result.
    Turn starts (HumanMessage) are preferred; agent-step boundaries are used only when the current turn alone
    is over budget.
    """
    human = [i for i, m in enumerate(messages) if i > 0 and isinstance(m, HumanMessage)]
    steps = [i for i, m in enumerate(messages) if i > 0 and isinstance(m, AIMessage)]

    def first_fitting(candidates: list[int]) -> int | None:
        for i in candidates:
            if estimate_tokens(messages[i:]) <= keep_tokens:
                return i
        return None

    cut = first_fitting(human)
    if cut is not None:
        return cut
    cut = first_fitting(steps)
    if cut is not None:
        return cut
    # Even the last step is over budget: keep just that step.
    return steps[-1] if steps else 0


def render_transcript(messages: list[AnyMessage]) -> str:
    lines: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            lines.append(f"USER: {m.text}")
        elif isinstance(m, AIMessage):
            if m.text:
                lines.append(f"AGENT: {m.text}")
            for call in m.tool_calls:
                lines.append(f"AGENT CALLS {call['name']}({truncate(str(call['args']), 500)})")
        elif isinstance(m, ToolMessage):
            lines.append(f"TOOL RESULT ({m.name}): {truncate(m.text, TOOL_RESULT_CHARS)}")
    return truncate("\n".join(lines), TRANSCRIPT_CHARS)


async def summarize(model: BaseChatModel, messages: list[AnyMessage], previous_summary: str) -> str:
    content = f"Previous summary:\n{previous_summary or '(none)'}\n\nConversation to summarize:\n{render_transcript(messages)}"
    response = await model.ainvoke([SystemMessage(SUMMARY_PROMPT), HumanMessage(content)])
    summary = response.text.strip()
    if not summary:
        raise ValueError("empty summary")
    return summary


def fallback_summary(messages: list[AnyMessage], previous_summary: str) -> str:
    """Used when the model cannot summarize: keep the user's requests verbatim, drop the rest."""
    requests = [f"- {truncate(m.text, 300)}" for m in messages if isinstance(m, HumanMessage)]
    parts = [previous_summary] if previous_summary else []
    parts.append("Earlier user requests (details of the work were dropped to save space):\n" + "\n".join(requests))
    return "\n\n".join(parts)


REASONING_BLOCKS = {"thinking", "redacted_thinking", "reasoning"}
# response_metadata key recording which "provider:model" produced an AIMessage.
PRODUCED_BY = "coding_agent_model"


def _text_only(message: AIMessage) -> AIMessage:
    return message.model_copy(update={"content": message.text})


def _without_reasoning(message: AIMessage) -> AIMessage:
    if not isinstance(message.content, list):
        return message
    content = [b for b in message.content if not (isinstance(b, dict) and b.get("type") in REASONING_BLOCKS)]
    if len(content) == len(message.content):
        return message
    return message.model_copy(update={"content": content})


def prepare_messages(messages: list[AnyMessage], provider: str) -> list[AnyMessage]:
    """Make stored history safe to send to `provider`.

    - Reasoning/thinking blocks are removed from turns before the current one. Removing them from the front
      of the history is allowed by providers that bind thinking to the conversation, and older reasoning is
      not useful once a turn is finished.
    - Messages produced by a different provider (after a model switch) are reduced to text plus the
      structured tool_calls, since provider-specific content blocks don't translate.
    """
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    prepared: list[AnyMessage] = []
    for i, message in enumerate(messages):
        if isinstance(message, AIMessage):
            producer = (message.response_metadata or {}).get(PRODUCED_BY, "")
            if producer and producer.partition(":")[0] != provider:
                message = _text_only(message)
            elif i < last_human:
                message = _without_reasoning(message)
        prepared.append(message)
    return prepared
