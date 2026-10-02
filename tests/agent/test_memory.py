import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from coding_agent.agent.fake import ScriptedChatModel
from coding_agent.agent.graph import build_graph
from coding_agent.server.events import stream_events
from coding_agent.services.memory import MAX_MEMORY_CHARS, MemoryError, MemoryStore, load_instructions

CONFIG = {"configurable": {"thread_id": "mem"}}


async def run(graph, text):
    return [e async for e in stream_events(graph, {"messages": [HumanMessage(text)], "iterations": 0}, CONFIG)]


def capture_system_prompt(seen):
    def respond(messages):
        seen.append(messages[0].content)
        return {"content": "ok"}

    return respond


def test_instructions_priority(workspace, workspace_dir):
    assert load_instructions(workspace) == (None, "")
    (workspace_dir / ".coding-agent").mkdir()
    (workspace_dir / ".coding-agent" / "instructions.md").write_text("third")
    (workspace_dir / "CLAUDE.md").write_text("second")
    assert load_instructions(workspace) == ("CLAUDE.md", "second")
    (workspace_dir / "AGENTS.md").write_text("Use tabs.")
    assert load_instructions(workspace) == ("AGENTS.md", "Use tabs.")


def test_memory_store(settings, tmp_path):
    store = MemoryStore(settings.state_dir, settings.workspace)
    other = MemoryStore(settings.state_dir, tmp_path)
    store.add("project", "Use pnpm, not npm")
    store.add("global", "No emojis in commits")
    assert store.read("project") == "- Use pnpm, not npm"
    assert other.read("project") == ""  # project memory is per workspace
    assert other.read("global") == "- No emojis in commits"
    assert "Already" in store.add("project", "Use  pnpm, not npm")
    assert not str(store.path("project")).startswith(str(settings.workspace))  # never inside the repo
    with pytest.raises(MemoryError, match="secret"):
        store.add("project", "the key is sk-abcdefghijklmnopqrstuv")
    with pytest.raises(MemoryError, match="empty"):
        store.add("project", "   ")
    store.clear("project")
    assert store.read("project") == ""


def test_memory_is_capped(settings):
    store = MemoryStore(settings.state_dir, settings.workspace)
    with pytest.raises(MemoryError, match="full"):
        for i in range(MAX_MEMORY_CHARS // 20):
            store.add("project", f"note number {i} " + "x" * 30)


async def test_instructions_and_memory_reach_the_prompt(settings, workspace_dir):
    seen = []
    graph = build_graph(settings, ScriptedChatModel(responder=capture_system_prompt(seen)), checkpointer=InMemorySaver())
    (workspace_dir / "AGENTS.md").write_text("Always run `make check` before finishing.")
    MemoryStore(settings.state_dir, settings.workspace).add("project", "User prefers short answers")
    await run(graph, "hi")
    prompt = seen[-1]
    assert "Project instructions (from AGENTS.md)" in prompt and "make check" in prompt
    assert "User prefers short answers" in prompt

    # Edits apply on the next call without restarting.
    (workspace_dir / "AGENTS.md").write_text("Use pytest -x.")
    await run(graph, "again")
    assert "pytest -x" in seen[-1] and "make check" not in seen[-1]


async def test_remember_tool_needs_no_approval(settings):
    model = ScriptedChatModel(
        turns=[
            {"tool_calls": [{"name": "remember", "args": {"note": "Tests live in tests/unit", "scope": "project"}}]},
            {"content": "Noted."},
        ]
    )
    graph = build_graph(settings, model, checkpointer=InMemorySaver())
    events = await run(graph, "remember where tests are")
    assert not any(e["type"] == "approval_required" for e in events)
    assert next(e for e in events if e["type"] == "tool_end")["status"] == "success"
    assert "Tests live in tests/unit" in MemoryStore(settings.state_dir, settings.workspace).read("project")
