"""Deterministic chat model for tests, evals and end-to-end runs without an API key.

A script is a list of turns; each turn is an AIMessage (or a dict with `content` and optional `tool_calls`).
Alternatively pass a `responder(messages) -> AIMessage` callable for dynamic behaviour.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


def to_ai_message(turn: AIMessage | dict) -> AIMessage:
    if isinstance(turn, AIMessage):
        return turn
    calls = [
        {"name": c["name"], "args": c.get("args", {}), "id": c.get("id") or f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
        for c in turn.get("tool_calls", [])
    ]
    usage = turn.get("usage") or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return AIMessage(content=turn.get("content", ""), tool_calls=calls, usage_metadata=usage)


class ScriptedChatModel(BaseChatModel):
    turns: list[Any] = []
    responder: Callable[[list[BaseMessage]], AIMessage | dict] | None = None
    _index: int = PrivateAttr(default=0)

    @classmethod
    def from_file(cls, path: Path) -> ScriptedChatModel:
        return cls(turns=json.loads(Path(path).read_text()))

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        if self.responder is not None:
            return to_ai_message(self.responder(messages))
        if self._index >= len(self.turns):
            return AIMessage(content="(script exhausted)")
        turn = self.turns[self._index]
        self._index += 1
        return to_ai_message(turn)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._next(messages)
        # Fresh ids per call so add_messages appends instead of replacing.
        message = message.model_copy(update={"id": f"run-{uuid.uuid4().hex[:12]}"})
        return ChatResult(generations=[ChatGeneration(message=message)])
