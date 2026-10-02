from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from coding_agent.agent.context import estimate_tokens, find_cut
from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.agent.graph import build_graph
from coding_agent.server.events import stream_events

CONFIG = {"configurable": {"thread_id": "ctx"}}
BIG = "lorem ipsum " * 400  # roughly 1.2k tokens


def turn(i):
    return [
        HumanMessage(f"request {i} {BIG}", id=f"h{i}"),
        AIMessage("", tool_calls=[{"name": "list_files", "args": {}, "id": f"c{i}"}], id=f"a{i}"),
        ToolMessage(BIG, tool_call_id=f"c{i}", name="list_files", id=f"t{i}"),
        AIMessage(f"answer {i}", id=f"f{i}"),
    ]


def test_find_cut_prefers_turn_boundaries():
    messages = [m for i in range(4) for m in turn(i)]
    cut = find_cut(messages, keep_tokens=estimate_tokens(messages[-4:]) + 10)
    assert isinstance(messages[cut], HumanMessage)
    assert messages[cut].id == "h3"


def test_find_cut_never_splits_tool_pairs():
    # One huge turn: the cut has to fall inside it, but only at an agent step.
    messages = [HumanMessage("go", id="h")]
    for i in range(5):
        messages += [
            AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": f"c{i}"}], id=f"a{i}"),
            ToolMessage(BIG, tool_call_id=f"c{i}", name="read_file", id=f"t{i}"),
        ]
    cut = find_cut(messages, keep_tokens=estimate_tokens(messages[-4:]) + 10)
    assert isinstance(messages[cut], AIMessage)
    kept_calls = {c["id"] for m in messages[cut:] if isinstance(m, AIMessage) for c in m.tool_calls}
    kept_results = {m.tool_call_id for m in messages[cut:] if isinstance(m, ToolMessage)}
    assert kept_calls == kept_results


def responder_recording(seen, summary="SUMMARY: user asked for requests 0-2"):
    def respond(messages):
        if isinstance(messages[0], SystemMessage) and "compress the earlier part" in messages[0].content:
            seen["summarized"] = messages[1].content
            return {"content": summary}
        seen["last_input"] = messages
        return {"content": "ok"}

    return respond


async def run(graph, text):
    return [e async for e in stream_events(graph, {"messages": [HumanMessage(text)], "iterations": 0}, CONFIG)]


async def test_compaction_summarizes_and_removes_old_messages(settings):
    seen = {}
    s = settings.with_overrides(context_token_limit=3000, context_keep_tokens=1500)
    graph = build_graph(s, ScriptedChatModel(responder=responder_recording(seen)), checkpointer=InMemorySaver())
    await graph.aupdate_state(CONFIG, {"messages": [m for i in range(3) for m in turn(i)]}, as_node="agent")

    events = await run(graph, "next request")
    compacted = next(e for e in events if e["type"] == "context_compacted")
    assert compacted["removed"] > 0 and compacted["fallback"] is False
    # The summary call is hidden from the UI token stream.
    assert not any(e["type"] == "agent_token" and "SUMMARY" in e["content"] for e in events)
    assert "request 0" in seen["summarized"]

    state = await graph.aget_state(CONFIG)
    assert state.values["summary"].startswith("SUMMARY")
    ids = [m.id for m in state.values["messages"]]
    assert "h0" not in ids
    system = seen["last_input"][0].content
    assert "Summary of the earlier conversation" in system and "SUMMARY:" in system
    assert estimate_tokens(state.values["messages"]) < estimate_tokens([m for i in range(3) for m in turn(i)])


async def test_compaction_falls_back_when_summary_fails(settings):
    def respond(messages):
        if "compress the earlier part" in messages[0].content:
            raise RuntimeError("summary model down")
        return {"content": "ok"}

    s = settings.with_overrides(context_token_limit=3000, context_keep_tokens=1500)
    graph = build_graph(s, ScriptedChatModel(responder=respond), checkpointer=InMemorySaver())
    await graph.aupdate_state(CONFIG, {"messages": [m for i in range(3) for m in turn(i)]}, as_node="agent")
    events = await run(graph, "next")
    assert next(e for e in events if e["type"] == "context_compacted")["fallback"] is True
    assert events[-1] == {"type": "final", "content": "ok"}
    state = await graph.aget_state(CONFIG)
    assert "Earlier user requests" in state.values["summary"] and "request 0" in state.values["summary"]


async def test_no_compaction_under_budget(settings):
    seen = {}
    graph = build_graph(settings, ScriptedChatModel(responder=responder_recording(seen)), checkpointer=InMemorySaver())
    events = await run(graph, "hi")
    assert not any(e["type"] == "context_compacted" for e in events)
    assert "summarized" not in seen
