from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.agent.graph import build_graph, open_runtime
from coding_agent.server.events import stream_events

CONFIG = {"configurable": {"thread_id": "rules"}}


def call(name, **args):
    return {"name": name, "args": args}


async def collect(graph, graph_input):
    return [e async for e in stream_events(graph, graph_input, CONFIG)]


def user(text):
    return {"messages": [HumanMessage(text)], "iterations": 0}


def approvals(events):
    return [e for e in events if e["type"] == "approval_required"]


async def test_always_allow_skips_repeat_prompts(settings, workspace_dir):
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [call("write_file", path="a.py", content="a")]},
            {"tool_calls": [call("write_file", path="b.py", content="b")]},
            {"content": "done"},
        ]
    )
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    [first] = approvals(await collect(graph, user("go")))
    assert first["allow_always"] is True and first["rule"] == "write_file"
    events = await collect(graph, Command(resume={"approved": True, "always": True}))
    assert approvals(events) == []  # the second write_file was auto-approved
    assert (workspace_dir / "b.py").read_text() == "b"
    assert (await graph.aget_state(CONFIG)).values["allow_rules"] == ["write_file"]


async def test_run_command_rules_match_exact_command(settings):
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [call("run_command", command="python -c 'print(1)'")]},
            {"tool_calls": [call("run_command", command="python -c 'print(1)'")]},
            {"tool_calls": [call("run_command", command="python -c 'print(2)'")]},
            {"content": "done"},
        ]
    )
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    await collect(graph, user("go"))
    events = await collect(graph, Command(resume={"approved": True, "always": True}))
    [pending] = approvals(events)  # same command ran without asking; a different one asks
    assert pending["arguments"]["command"] == "python -c 'print(2)'"


async def test_high_risk_never_always_allowed(settings):
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [call("run_command", command="git reset --hard")]},
            {"tool_calls": [call("run_command", command="git reset --hard")]},
            {"content": "done"},
        ]
    )
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    [first] = approvals(await collect(graph, user("go")))
    assert first["allow_always"] is False
    events = await collect(graph, Command(resume={"approved": True, "always": True}))
    assert len(approvals(events)) == 1
    assert (await graph.aget_state(CONFIG)).values["allow_rules"] == []


async def test_rules_persist_across_restart(settings, workspace_dir):
    async with open_runtime(settings, ScriptedChatModel(turns=[{"tool_calls": [call("write_file", path="a.py", content="a")]}])) as rt:
        await collect(rt.graph, user("go"))
        await collect(rt.graph, Command(resume={"approved": True, "always": True}))
    turns = [{"tool_calls": [call("edit_file", path="a.py", old_string="a", new_string="b")]}, {"content": "done"}]
    async with open_runtime(settings, ScriptedChatModel(turns=turns)) as rt:
        events = await collect(rt.graph, user("edit"))
        # edit_file is a different rule than write_file, so it still asks.
        assert approvals(events)[0]["rule"] == "edit_file"
        assert (await rt.graph.aget_state(CONFIG)).values["allow_rules"] == ["write_file"]
