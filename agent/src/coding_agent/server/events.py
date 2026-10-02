"""Translate LangGraph stream chunks into the UI event protocol.

Event types: run_start, agent_token, agent_message, tool_start, tool_output, tool_end,
approval_required, usage, final, error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langgraph.graph.state import CompiledStateGraph

from coding_agent.utils.logging import get_logger

log = get_logger("events")


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def add_usage(total: dict[str, int], usage: dict | None) -> None:
    if not usage:
        return
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key) or 0)


async def stream_events(graph: CompiledStateGraph, graph_input: Any, config: dict) -> AsyncIterator[dict]:
    """Run the graph and yield UI events until it finishes or pauses for approval."""
    usage: dict[str, int] = {}
    final_text = ""
    interrupted = False
    yield {"type": "run_start", "session_id": config["configurable"]["thread_id"]}
    try:
        async for mode, chunk in graph.astream(graph_input, config, stream_mode=["messages", "updates", "custom"]):
            if mode == "messages":
                message, meta = chunk
                if meta.get("langgraph_node") == "agent" and isinstance(message, AIMessageChunk | AIMessage):
                    text = message_text(message)
                    if text:
                        yield {"type": "agent_token", "content": text}
            elif mode == "custom":
                yield chunk
            elif mode == "updates":
                for interrupt in chunk.get("__interrupt__", ()):
                    interrupted = True
                    yield {**interrupt.value, "interrupt_id": interrupt.id}
                update = chunk.get("agent")
                if update and update.get("messages"):
                    message = update["messages"][-1]
                    add_usage(usage, getattr(message, "usage_metadata", None))
                    text = message_text(message)
                    if text:
                        final_text = text
                        yield {"type": "agent_message", "content": text}
    except Exception as exc:
        log.exception("agent run failed", extra={"session_id": config["configurable"]["thread_id"]})
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        return
    if any(usage.values()):
        yield {"type": "usage", **usage}
    if not interrupted:
        yield {"type": "final", "content": final_text}
