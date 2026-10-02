"""API tests for undo, compaction, sessions and memory."""

import json

import httpx
import pytest

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.server.api import create_app


def call(name, **args):
    return {"name": name, "args": args}


@pytest.fixture
async def make_client(settings):
    opened = []

    async def factory(turns=None, responder=None, settings_override=None):
        app = create_app(settings_override or settings, ScriptedChatModel(turns=turns or [], responder=responder))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        opened.append((client, lifespan))
        return client

    yield factory
    for client, lifespan in opened:
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


async def sse(client, url, body):
    events = []
    async with client.stream("POST", url, json=body) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def new_session(client):
    return (await client.post("/sessions")).json()["session_id"]


async def test_undo_restores_last_turn(make_client, workspace_dir):
    turns = [
        {"tool_calls": [call("edit_file", path="hello.py", old_string="hello", new_string="bye")]},
        {"content": "turn 1 done"},
        {"tool_calls": [call("write_file", path="new.py", content="x\n"), call("edit_file", path="hello.py", old_string="bye", new_string="ciao")]},
        {"content": "turn 2 done"},
    ]
    client = await make_client(turns=turns)
    sid = await new_session(client)
    await sse(client, f"/sessions/{sid}/messages", {"content": "one"})
    await sse(client, f"/sessions/{sid}/approval", {"approved": True, "always": True})
    events = await sse(client, f"/sessions/{sid}/messages", {"content": "two"})
    assert events[-1]["type"] == "approval_required" and events[-1]["action"] == "write_file"
    await sse(client, f"/sessions/{sid}/approval", {"approved": True})  # edit_file then runs via the always rule
    assert (workspace_dir / "hello.py").read_text() == "print('ciao')\n"
    assert (workspace_dir / "new.py").exists()

    result = (await client.post(f"/sessions/{sid}/undo")).json()
    assert result == {"restored": ["hello.py"], "deleted": ["new.py"], "conflicts": []}
    assert (workspace_dir / "hello.py").read_text() == "print('bye')\n"
    assert not (workspace_dir / "new.py").exists()

    history = (await client.get(f"/sessions/{sid}")).json()["messages"]
    assert "undid the agent's changes" in history[-1]["content"]

    # A second undo goes back one more turn.
    result = (await client.post(f"/sessions/{sid}/undo")).json()
    assert result["restored"] == ["hello.py"]
    assert (workspace_dir / "hello.py").read_text() == "print('hello')\n"
    assert (await client.post(f"/sessions/{sid}/undo")).json() == {"restored": [], "deleted": [], "conflicts": []}


async def test_undo_reports_conflicts(make_client, workspace_dir):
    client = await make_client(turns=[{"tool_calls": [call("write_file", path="hello.py", content="agent\n")]}, {"content": "ok"}])
    sid = await new_session(client)
    await sse(client, f"/sessions/{sid}/messages", {"content": "go"})
    await sse(client, f"/sessions/{sid}/approval", {"approved": True})
    (workspace_dir / "hello.py").write_text("user edited after\n")
    result = (await client.post(f"/sessions/{sid}/undo")).json()
    assert result["conflicts"] == ["hello.py"]
    assert (workspace_dir / "hello.py").read_text() == "user edited after\n"


async def test_undo_and_compact_refused_while_approval_pending(make_client):
    client = await make_client(turns=[{"tool_calls": [call("write_file", path="x.py", content="x")]}])
    sid = await new_session(client)
    await sse(client, f"/sessions/{sid}/messages", {"content": "go"})
    assert (await client.post(f"/sessions/{sid}/undo")).status_code == 409
    assert (await client.post(f"/sessions/{sid}/compact")).status_code == 409


async def test_manual_compact(make_client):
    def respond(messages):
        if "compress the earlier part" in messages[0].content:
            return {"content": "short summary"}
        return {"content": "answer " + "z" * 200}

    client = await make_client(responder=respond)
    sid = await new_session(client)
    for i in range(3):
        await sse(client, f"/sessions/{sid}/messages", {"content": f"question {i}"})
    result = (await client.post(f"/sessions/{sid}/compact")).json()
    assert result["removed"] > 0
    session = (await client.get(f"/sessions/{sid}")).json()
    assert session["summary"] == "short summary"
    assert session["messages"][0]["content"] == "question 2"


async def test_sessions_listing(make_client, settings, tmp_path):
    client = await make_client(responder=lambda _m: {"content": "ok"})
    first, second = await new_session(client), await new_session(client)
    await sse(client, f"/sessions/{first}/messages", {"content": "Fix   the login\nbug please"})
    await sse(client, f"/sessions/{second}/messages", {"content": "Add a feature"})
    await sse(client, f"/sessions/{first}/messages", {"content": "and also tests"})
    sessions = (await client.get("/sessions")).json()["sessions"]
    assert [s["session_id"] for s in sessions] == [first, second]  # most recently active first
    assert sessions[0]["title"] == "Fix the login bug please"

    other_ws = tmp_path / "other"
    other_ws.mkdir()
    other = await make_client(settings_override=settings.with_overrides(workspace=other_ws))
    assert (await other.get("/sessions")).json()["sessions"] == []


async def test_memory_endpoints(make_client, workspace_dir):
    client = await make_client(turns=[{"tool_calls": [call("remember", note="Prefers pytest -q")]}, {"content": "ok"}])
    (workspace_dir / "AGENTS.md").write_text("rules")
    sid = await new_session(client)
    await sse(client, f"/sessions/{sid}/messages", {"content": "remember this"})
    memory = (await client.get("/memory")).json()
    assert memory == {"instructions_source": "AGENTS.md", "project": "- Prefers pytest -q", "global": ""}
    assert (await client.delete("/memory", params={"scope": "project"})).json() == {"cleared": "project"}
    assert (await client.get("/memory")).json()["project"] == ""
    assert (await client.delete("/memory", params={"scope": "everything"})).status_code == 422
