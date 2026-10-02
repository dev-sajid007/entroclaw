from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.agent.graph import build_graph, open_runtime
from coding_agent.agent.nodes import repair_dangling_tool_calls
from coding_agent.server.events import stream_events

CONFIG = {"configurable": {"thread_id": "t1"}}


def call(name, **args):
    return {"name": name, "args": args}


async def collect(graph, graph_input, config=CONFIG):
    return [event async for event in stream_events(graph, graph_input, config)]


def approval_of(events):
    return next(e for e in events if e["type"] == "approval_required")


def user(text):
    return {"messages": [HumanMessage(text)], "iterations": 0}


async def test_answer_without_tools(settings):
    graph = build_graph(settings, ScriptedChatModel(turns=[{"content": "Hello!"}]), checkpointer=InMemorySaver())
    events = await collect(graph, user("hi"))
    assert events[-1] == {"type": "final", "content": "Hello!"}
    assert any(e["type"] == "agent_token" for e in events)


async def test_tool_loop_reads_file_and_answers(settings):
    model = ScriptedChatModel(turns=[{"tool_calls": [call("read_file", path="hello.py")]}, {"content": "It prints hello."}])
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    events = await collect(graph, user("what does hello.py do?"))
    types = [e["type"] for e in events]
    assert types.index("tool_start") < types.index("tool_end") < types.index("final")
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["status"] == "success" and "print('hello')" in tool_end["result"]
    state = await graph.aget_state(CONFIG)
    assert isinstance(state.values["messages"][2], ToolMessage)
    assert state.values["iterations"] == 2


async def test_write_requires_approval_and_shows_diff(settings, workspace_dir):
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [call("edit_file", path="hello.py", old_string="hello", new_string="world")]},
            {"content": "Done."},
        ]
    )
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    events = await collect(graph, user("change it"))
    approval = approval_of(events)
    assert approval["action"] == "edit_file"
    assert "+print('world')" in approval["diff"]
    assert not any(e["type"] == "final" for e in events)
    assert "hello" in (workspace_dir / "hello.py").read_text()

    events = await collect(graph, Command(resume={"approved": True}))
    assert (workspace_dir / "hello.py").read_text() == "print('world')\n"
    assert events[-1] == {"type": "final", "content": "Done."}


async def test_rejection_is_reported_to_model(settings, workspace_dir):
    seen = {}

    def responder(messages):
        last = messages[-1]
        if isinstance(last, HumanMessage):
            return {"tool_calls": [call("write_file", path="x.py", content="x")]}
        seen["tool_result"] = last.content
        return {"content": "OK, I won't."}

    graph = build_graph(settings, ScriptedChatModel(responder=responder), checkpointer=InMemorySaver())
    await collect(graph, user("make x.py"))
    events = await collect(graph, Command(resume={"approved": False, "feedback": "use y.py instead"}))
    assert not (workspace_dir / "x.py").exists()
    assert "rejected" in seen["tool_result"] and "use y.py instead" in seen["tool_result"]
    assert any(e["type"] == "tool_end" and e["status"] == "rejected" for e in events)


async def test_denied_command_never_runs_or_asks(settings, workspace_dir):
    model = ScriptedChatModel(turns=[{"tool_calls": [call("run_command", command="sudo rm -rf /")]}, {"content": "I can't do that."}])
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    events = await collect(graph, user("wipe it"))
    assert not any(e["type"] == "approval_required" for e in events)
    denied = next(e for e in events if e["type"] == "tool_end")
    assert denied["status"] == "denied"
    assert events[-1]["type"] == "final"


async def test_multiple_approvals_in_one_turn(settings, workspace_dir):
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [call("write_file", path="a.py", content="a"), call("write_file", path="b.py", content="b")]},
            {"content": "done"},
        ]
    )
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    first = approval_of(await collect(graph, user("go")))
    second = approval_of(await collect(graph, Command(resume={"approved": True})))
    assert first["arguments"]["path"] == "a.py" and second["arguments"]["path"] == "b.py"
    events = await collect(graph, Command(resume={"approved": False}))
    assert (workspace_dir / "a.py").exists() and not (workspace_dir / "b.py").exists()
    assert events[-1]["type"] == "final"


async def test_unknown_tool_and_tool_errors_are_recoverable(settings):
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [call("does_not_exist"), call("read_file", path="missing.py")]},
            {"content": "recovered"},
        ]
    )
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    events = await collect(graph, user("go"))
    statuses = [e["status"] for e in events if e["type"] == "tool_end"]
    assert statuses == ["error", "error"]
    assert events[-1] == {"type": "final", "content": "recovered"}


async def test_max_iterations_stops_loop(settings):
    looping = ScriptedChatModel(responder=lambda _m: {"tool_calls": [call("list_files")]})
    graph = build_graph(settings.with_overrides(max_iterations=3), looping, checkpointer=InMemorySaver())
    events = await collect(graph, user("loop forever"))
    assert sum(e["type"] == "tool_start" for e in events) == 3
    assert "iteration limit" in events[-1]["content"]


async def test_checkpoint_persists_across_runtimes(settings):
    async with open_runtime(settings, ScriptedChatModel(turns=[{"tool_calls": [call("write_file", path="p.py", content="p")]}])) as rt:
        events = await collect(rt.graph, user("write p.py"))
        assert approval_of(events)["action"] == "write_file"

    # A fresh runtime (e.g. after a server restart) resumes the paused session from SQLite.
    async with open_runtime(settings, ScriptedChatModel(turns=[{"content": "resumed and done"}])) as rt:
        state = await rt.graph.aget_state(CONFIG)
        assert state.next == ("approval",)
        events = await collect(rt.graph, Command(resume={"approved": True}))
        assert events[-1] == {"type": "final", "content": "resumed and done"}
        assert (settings.workspace / "p.py").read_text() == "p"


def test_repair_dangling_tool_calls():
    ai = AIMessage(content="", tool_calls=[{"name": "list_files", "args": {}, "id": "c1"}])
    repaired = repair_dangling_tool_calls([HumanMessage("hi"), ai, HumanMessage("next")])
    assert isinstance(repaired[2], ToolMessage) and repaired[2].tool_call_id == "c1"
    assert isinstance(repaired[3], HumanMessage)
