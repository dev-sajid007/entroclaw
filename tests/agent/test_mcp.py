import json
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.agent.graph import open_runtime
from coding_agent.server.events import stream_events
from coding_agent.tools.mcp import load_mcp_tools, parse_config

SERVER = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_server.py"
CONFIG = {"configurable": {"thread_id": "mcp"}}


def write_config(tmp_path, servers):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": servers}))
    return path


def demo_server(**extra):
    return {"command": sys.executable, "args": [str(SERVER)], **extra}


def test_parse_config_expands_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "abc")
    path = write_config(tmp_path, {"demo": demo_server(env={"TOKEN": "${DEMO_TOKEN}"}, trusted_tools=["lookup_issue"])})
    connections, trusted = parse_config(path)
    assert connections["demo"]["transport"] == "stdio"
    assert connections["demo"]["env"] == {"TOKEN": "abc"}
    assert "trusted_tools" not in connections["demo"]
    assert trusted == {"demo": {"lookup_issue"}}


async def test_loads_tools_and_isolates_broken_servers(tmp_path):
    path = write_config(tmp_path, {"demo": demo_server(trusted_tools=["lookup_issue"]), "broken": {"command": "/nonexistent/binary"}})
    loaded = await load_mcp_tools(path)
    assert sorted(t.name for t in loaded.tools) == ["demo_close_issue", "demo_lookup_issue"]
    assert loaded.trusted == {"demo_lookup_issue"}
    assert "broken" in loaded.errors


async def test_agent_uses_mcp_tools_with_policy(settings, tmp_path):
    path = write_config(tmp_path, {"demo": demo_server(trusted_tools=["lookup_issue"])})
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [{"name": "demo_lookup_issue", "args": {"number": 7}}, {"name": "demo_close_issue", "args": {"number": 7}}]},
            {"content": "done"},
        ]
    )
    async with open_runtime(settings.with_overrides(mcp_config=path), model) as rt:
        assert "demo_lookup_issue" in rt.tool_names
        events = [e async for e in stream_events(rt.graph, {"messages": [HumanMessage("check #7")], "iterations": 0}, CONFIG)]
        approval = next(e for e in events if e["type"] == "approval_required")
        assert approval["action"] == "demo_close_issue"  # untrusted MCP tool needs approval
        events = [e async for e in stream_events(rt.graph, Command(resume={"approved": True}), CONFIG)]
        results = {e["tool"]: e["result"] for e in events if e["type"] == "tool_end"}
        assert "password contains a space" in results["demo_lookup_issue"]
        assert "Closed #7" in results["demo_close_issue"]
