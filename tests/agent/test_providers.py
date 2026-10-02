import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from coding_agent.agent.context import PRODUCED_BY, prepare_messages
from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.agent.graph import build_agent
from coding_agent.agent.llm import ConfigurationError, build_model, parse_spec
from coding_agent.server.events import stream_events

CONFIG = {"configurable": {"thread_id": "prov"}}


def test_parse_spec():
    assert parse_spec("anthropic:claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    assert parse_spec("gpt-4.1-mini") == ("openai", "gpt-4.1-mini")
    assert parse_spec("llama3:8b", "ollama") == ("ollama", "llama3:8b")  # ":" inside a model name
    assert parse_spec("ollama:llama3:8b") == ("ollama", "llama3:8b")


def test_settings_model_specs(settings):
    s = settings.with_overrides(model="anthropic:claude-opus-5-5", models=("openai:gpt-4.1-mini", "anthropic:claude-opus-5-5"))
    assert s.default_model_spec == "anthropic:claude-opus-5-5"
    assert s.available_models == ["anthropic:claude-opus-5-5", "openai:gpt-4.1-mini"]
    assert settings.default_model_spec == "openai:gpt-4.1-mini"


def test_missing_key_per_provider(settings, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        build_model(settings, "anthropic:claude-opus-5-5")
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        build_model(settings)


def test_anthropic_request_payload(settings, monkeypatch):
    """Offline check of what we send to Claude: no sampling params, adaptive thinking with effort, tolerant
    thinking-block binding, refusal fallback, large max_tokens with streaming, cached system prompt."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = build_model(settings, "anthropic:claude-opus-5-5")
    system = SystemMessage(content=[{"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}}])
    payload = model._get_request_payload([system, HumanMessage("hi")])
    assert payload["model"] == "claude-opus-5-5"
    assert payload["max_tokens"] == 64000
    assert "temperature" not in payload and "top_p" not in payload and "top_k" not in payload
    assert payload["thinking"] == {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert payload["output_config"] == {"effort": "high"}
    assert payload["fallbacks"] == "default"
    assert set(payload["betas"]) == {"thinking-binding-controls-2026-08-01", "server-side-fallback-2026-07-01"}
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert model.streaming is True

    off = build_model(settings.with_overrides(anthropic_fallbacks="off", model_effort="xhigh"), "anthropic:claude-opus-5-5")
    payload = off._get_request_payload([HumanMessage("hi")])
    assert "fallbacks" not in payload and payload["betas"] == ["thinking-binding-controls-2026-08-01"]
    assert payload["output_config"] == {"effort": "xhigh"}


def ai(content, producer="anthropic:claude-opus-5-5", **kw):
    return AIMessage(content=content, response_metadata={PRODUCED_BY: producer}, **kw)


THINKING = {"type": "thinking", "thinking": "", "signature": "sig"}


def test_prepare_messages_strips_old_reasoning_only():
    call = {"name": "read_file", "args": {"path": "a"}, "id": "c1", "type": "tool_call"}
    history = [
        HumanMessage("first"),
        ai([THINKING, {"type": "text", "text": "old answer"}]),
        HumanMessage("second"),
        ai([THINKING, {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a"}}], tool_calls=[call]),
        ToolMessage("contents", tool_call_id="c1"),
    ]
    prepared = prepare_messages(history, "anthropic")
    assert prepared[1].content == [{"type": "text", "text": "old answer"}]  # earlier turn: thinking removed
    assert prepared[3].content[0] == THINKING  # current turn keeps its thinking
    assert prepared[3].tool_calls == [call]
    assert history[1].content[0] == THINKING  # stored history is not mutated


def test_prepare_messages_after_model_switch():
    call = {"name": "list_files", "args": {}, "id": "c1", "type": "tool_call"}
    history = [
        HumanMessage("go"),
        ai(
            [THINKING, {"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "c1", "name": "list_files", "input": {}}],
            tool_calls=[call],
        ),
        ToolMessage("files", tool_call_id="c1"),
    ]
    prepared = prepare_messages(history, "openai")
    assert prepared[1].content == "Looking."
    assert prepared[1].tool_calls == [call]


async def test_per_session_model_switch(settings):
    seen = []

    def factory(spec):
        return ScriptedChatModel(responder=lambda _m: (seen.append(spec), {"content": f"from {spec}"})[1])

    s = settings.with_overrides(models=("anthropic:claude-opus-5-5",))
    graph, nodes = build_agent(s, factory(s.default_model_spec), checkpointer=InMemorySaver(), model_factory=factory)
    events = [e async for e in stream_events(graph, {"messages": [HumanMessage("a")], "iterations": 0}, CONFIG)]
    assert events[-1]["content"] == "from openai:gpt-4.1-mini"

    await graph.aupdate_state(CONFIG, {"model": "anthropic:claude-opus-5-5"}, as_node="agent")
    events = [e async for e in stream_events(graph, {"messages": [HumanMessage("b")], "iterations": 0}, CONFIG)]
    assert events[-1]["content"] == "from anthropic:claude-opus-5-5"
    state = await graph.aget_state(CONFIG)
    producers = [m.response_metadata[PRODUCED_BY] for m in state.values["messages"] if isinstance(m, AIMessage)]
    assert producers == ["openai:gpt-4.1-mini", "anthropic:claude-opus-5-5"]


async def test_system_prompt_is_cached_for_anthropic(settings):
    captured = []

    def factory(spec):
        return ScriptedChatModel(responder=lambda m: (captured.append(m[0]), {"content": "ok"})[1])

    graph, _ = build_agent(settings, factory("x"), checkpointer=InMemorySaver(), model_factory=factory)
    await graph.aupdate_state(CONFIG, {"model": "anthropic:claude-opus-5-5", "summary": "earlier stuff"}, as_node="agent")
    [e async for e in stream_events(graph, {"messages": [HumanMessage("hi")], "iterations": 0}, CONFIG)]
    blocks = captured[-1].content
    assert blocks[0]["cache_control"] == {"type": "ephemeral"} and "Coding Agent" in blocks[0]["text"]
    assert "earlier stuff" in blocks[1]["text"] and "cache_control" not in blocks[1]  # volatile part after the cache point


async def test_refusal_is_visible(settings):
    refusal = AIMessage(content="", response_metadata={"stop_reason": "refusal", "stop_details": {"category": "cyber"}})
    graph, _ = build_agent(settings, ScriptedChatModel(turns=[refusal]), checkpointer=InMemorySaver())
    events = [e async for e in stream_events(graph, {"messages": [HumanMessage("x")], "iterations": 0}, CONFIG)]
    assert "declined this request (category: cyber)" in events[-1]["content"]
