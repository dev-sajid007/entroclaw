import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.agent.graph import build_graph
from coding_agent.server.events import stream_events
from coding_agent.tools.todos import TodoError, validate_todos

CONFIG = {"configurable": {"thread_id": "plan"}}


def call(name, **args):
    return {"name": name, "args": args}


async def run(graph, text):
    return [e async for e in stream_events(graph, {"messages": [HumanMessage(text)], "iterations": 0}, CONFIG)]


TODOS = [
    {"content": "Read login code", "status": "completed"},
    {"content": "Fix the condition", "status": "in_progress"},
    {"content": "Run tests", "status": "pending"},
]


async def test_plan_mode_allows_reads_and_denies_writes(settings, workspace_dir):
    seen = {}

    def respond(messages):
        if isinstance(messages[-1], HumanMessage):
            seen["system"] = messages[0].content
            return {
                "tool_calls": [
                    call("read_file", path="hello.py"),
                    call("run_command", command="pytest -q"),
                    call("update_todos", todos=TODOS),
                    call("write_file", path="new.py", content="x"),
                    call("run_command", command="make install"),
                ]
            }
        return {"content": "Here is my plan."}

    graph = build_graph(settings, ScriptedChatModel(responder=respond), checkpointer=InMemorySaver())
    await graph.aupdate_state(CONFIG, {"mode": "plan"}, as_node="agent")
    events = await run(graph, "fix login")

    assert "Plan mode is ON" in seen["system"]
    assert not any(e["type"] == "approval_required" for e in events)  # denied outright, never asks
    statuses = [(e["tool"], e["status"]) for e in events if e["type"] == "tool_end"]
    assert ("read_file", "success") in statuses
    assert ("update_todos", "success") in statuses
    assert ("write_file", "denied") in statuses
    assert [s for t, s in statuses if t == "run_command"] == ["success", "denied"]  # allowlisted runs, others don't
    assert not (workspace_dir / "new.py").exists()
    denied = next(e for e in events if e["type"] == "tool_end" and e["tool"] == "write_file")
    assert "Plan mode" in denied["result"]


async def test_build_mode_is_unchanged(settings):
    graph = build_graph(
        settings, ScriptedChatModel(turns=[{"tool_calls": [call("write_file", path="a.py", content="a")]}]), checkpointer=InMemorySaver()
    )
    events = await run(graph, "go")
    assert any(e["type"] == "approval_required" for e in events)


async def test_update_todos_updates_state_and_streams(settings):
    graph = build_graph(
        settings, ScriptedChatModel(turns=[{"tool_calls": [call("update_todos", todos=TODOS)]}, {"content": "ok"}]), checkpointer=InMemorySaver()
    )
    events = await run(graph, "go")
    assert next(e for e in events if e["type"] == "todos")["todos"] == TODOS
    end = next(e for e in events if e["type"] == "tool_end")
    assert end["status"] == "success" and "1/3 done" in end["result"]
    assert (await graph.aget_state(CONFIG)).values["todos"] == TODOS


async def test_invalid_todos_are_reported_to_the_model(settings):
    bad = [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}]
    graph = build_graph(
        settings, ScriptedChatModel(turns=[{"tool_calls": [call("update_todos", todos=bad)]}, {"content": "ok"}]), checkpointer=InMemorySaver()
    )
    events = await run(graph, "go")
    end = next(e for e in events if e["type"] == "tool_end")
    assert end["status"] == "error" and "only one item" in end["result"]
    assert "todos" not in (await graph.aget_state(CONFIG)).values


def test_validate_todos():
    assert validate_todos([{"content": "  a   b ", "status": "pending"}]) == [{"content": "a b", "status": "pending"}]
    for bad in ["x", [{"content": ""}], [{"content": "a", "status": "done"}], [{"content": str(i)} for i in range(31)]]:
        with pytest.raises(TodoError):
            validate_todos(bad)
