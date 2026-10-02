import json

import httpx
import pytest

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.server.api import create_app


def call(name, **args):
    return {"name": name, "args": args}


@pytest.fixture
async def make_client(settings):
    clients = []

    async def factory(turns=None, responder=None, token=None, monkeypatch=None):
        app = create_app(settings, ScriptedChatModel(turns=turns or [], responder=responder))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        clients.append((client, lifespan))
        return client

    yield factory
    for client, lifespan in clients:
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


async def sse_events(client, url, body):
    events = []
    async with client.stream("POST", url, json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def test_health(make_client):
    client = await make_client()
    body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert {"read_file", "run_command", "git_status"} <= set(body["tools"])


async def test_message_stream_and_history(make_client):
    client = await make_client(turns=[{"tool_calls": [call("list_files")]}, {"content": "Here are the files."}])
    sid = (await client.post("/sessions")).json()["session_id"]
    events = await sse_events(client, f"/sessions/{sid}/messages", {"content": "list files"})
    types = [e["type"] for e in events]
    assert types[0] == "run_start"
    assert "tool_start" in types and "tool_end" in types
    assert events[-1] == {"type": "final", "content": "Here are the files."}

    history = (await client.get(f"/sessions/{sid}")).json()
    assert [m["role"] for m in history["messages"]] == ["user", "agent", "tool", "agent"]
    assert history["pending_approval"] is None


async def test_approval_flow(make_client, workspace_dir):
    client = await make_client(turns=[{"tool_calls": [call("write_file", path="new.py", content="x = 1\n")]}, {"content": "Created."}])
    sid = (await client.post("/sessions")).json()["session_id"]
    events = await sse_events(client, f"/sessions/{sid}/messages", {"content": "create new.py"})
    approval = next(e for e in events if e["type"] == "approval_required")
    assert "+x = 1" in approval["diff"]

    pending = (await client.get(f"/sessions/{sid}")).json()["pending_approval"]
    assert pending["action"] == "write_file"

    events = await sse_events(client, f"/sessions/{sid}/approval", {"approved": True})
    assert events[-1] == {"type": "final", "content": "Created."}
    assert (workspace_dir / "new.py").read_text() == "x = 1\n"


async def test_approval_without_pending_is_conflict(make_client):
    client = await make_client()
    response = await client.post("/sessions/nope/approval", json={"approved": True})
    assert response.status_code == 409


async def test_new_message_while_pending_rejects_with_feedback(make_client, workspace_dir):
    seen = []

    def responder(messages):
        seen.append(messages[-1].content)
        if len(seen) == 1:
            return {"tool_calls": [call("write_file", path="a.py", content="a")]}
        return {"content": "Understood."}

    client = await make_client(responder=responder)
    sid = (await client.post("/sessions")).json()["session_id"]
    await sse_events(client, f"/sessions/{sid}/messages", {"content": "make a.py"})
    events = await sse_events(client, f"/sessions/{sid}/messages", {"content": "actually call it b.py"})
    assert not (workspace_dir / "a.py").exists()
    assert "actually call it b.py" in seen[-1]
    assert events[-1]["type"] == "final"


async def test_model_errors_become_error_events(make_client):
    def boom(_messages):
        raise RuntimeError("provider unavailable")

    client = await make_client(responder=boom)
    sid = (await client.post("/sessions")).json()["session_id"]
    events = await sse_events(client, f"/sessions/{sid}/messages", {"content": "hi"})
    assert events[-1]["type"] == "error"
    assert "provider unavailable" in events[-1]["message"]
    # The session is unlocked again after a failed run.
    events = await sse_events(client, f"/sessions/{sid}/messages", {"content": "hi again"})
    assert events[-1]["type"] == "error"


async def test_bearer_token(make_client, monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "s3cret")
    client = await make_client()
    assert (await client.get("/health")).status_code == 401
    assert (await client.get("/health", headers={"Authorization": "Bearer s3cret"})).status_code == 200
