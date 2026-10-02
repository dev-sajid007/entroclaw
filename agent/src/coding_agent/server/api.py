"""HTTP + SSE API between the CLI and the LangGraph runtime."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from coding_agent.agent.graph import Runtime, open_runtime
from coding_agent.agent.llm import ConfigurationError, configured_providers, parse_spec
from coding_agent.agent.nodes import ModelFactory
from coding_agent.config.settings import Settings
from coding_agent.server.events import message_text, stream_events
from coding_agent.services.history import FileHistory
from coding_agent.services.memory import SCOPES, MemoryStore, load_instructions
from coding_agent.services.sandbox import sandbox_status
from coding_agent.services.workspace import Workspace
from coding_agent.utils.logging import get_logger

log = get_logger("api")


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class ModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class ModeRequest(BaseModel):
    mode: Literal["build", "plan"]


class ApprovalRequest(BaseModel):
    approved: bool
    feedback: str = ""
    # Allow this kind of action for the rest of the session (ignored for high-risk actions).
    always: bool = False


def sse(events: AsyncIterator[dict], lock: asyncio.Lock) -> StreamingResponse:
    released = False

    def release() -> None:
        # Called from the stream's finally and as a background task, so the session is unlocked
        # even if the client disconnects before streaming starts.
        nonlocal released
        if not released:
            released = True
            lock.release()

    async def body() -> AsyncIterator[bytes]:
        try:
            async for event in events:
                yield f"data: {json.dumps(event, default=str)}\n\n".encode()
        finally:
            release()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(release),
    )


def create_app(settings: Settings, model: BaseChatModel | None = None, model_factory: ModelFactory | None = None) -> FastAPI:
    locks: dict[str, asyncio.Lock] = {}
    api_token = os.environ.get("AGENT_API_TOKEN")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_runtime(settings, model, model_factory) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title="Coding Agent", lifespan=lifespan)

    def authorize(request: Request) -> None:
        if not api_token:
            return
        header = request.headers.get("authorization", "")
        if not secrets.compare_digest(header, f"Bearer {api_token}"):
            raise HTTPException(401, "invalid or missing bearer token")

    def runtime_of(request: Request) -> Runtime:
        return request.app.state.runtime

    def config_for(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": settings.max_iterations * 4 + 10}

    async def pending_approval(runtime: Runtime, session_id: str) -> dict | None:
        state = await runtime.graph.aget_state(config_for(session_id))
        for task in state.tasks:
            for interrupt in task.interrupts:
                return {**interrupt.value, "interrupt_id": interrupt.id}
        return None

    async def acquire(session_id: str) -> asyncio.Lock:
        lock = locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "this session is already running")
        await lock.acquire()
        return lock

    @asynccontextmanager
    async def idle_session(runtime: Runtime, session_id: str):
        """Hold the session lock for an out-of-run operation; refuse while an approval is pending."""
        lock = await acquire(session_id)
        try:
            if await pending_approval(runtime, session_id):
                raise HTTPException(409, "answer the pending approval first")
            yield
        finally:
            lock.release()

    memory = MemoryStore(settings.state_dir, settings.workspace)

    @app.get("/health")
    async def health(request: Request, _: None = Depends(authorize)) -> dict:
        runtime = runtime_of(request)
        return {
            "status": "ok",
            "workspace": str(settings.workspace),
            "model": settings.default_model_spec if not settings.fake_model_script else "scripted",
            "models": settings.available_models,
            "providers": configured_providers(),
            "require_approval": settings.require_approval,
            "sandbox": sandbox_status(settings).describe(),
            "tools": runtime.tool_names,
            "mcp_errors": runtime.mcp_errors,
        }

    @app.post("/sessions")
    async def create_session(_: None = Depends(authorize)) -> dict:
        return {"session_id": uuid.uuid4().hex}

    @app.get("/sessions")
    async def list_sessions(request: Request, limit: int = 20, _: None = Depends(authorize)) -> dict:
        sessions = await runtime_of(request).sessions.list(str(settings.workspace), min(max(limit, 1), 100))
        return {"sessions": sessions}

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str, request: Request, _: None = Depends(authorize)) -> dict:
        runtime = runtime_of(request)
        state = await runtime.graph.aget_state(config_for(session_id))
        history = []
        for message in state.values.get("messages", []):
            if isinstance(message, HumanMessage):
                history.append({"role": "user", "content": message_text(message)})
            elif isinstance(message, AIMessage):
                history.append(
                    {
                        "role": "agent",
                        "content": message_text(message),
                        "tool_calls": [{"id": c["id"], "tool": c["name"], "args": c["args"]} for c in message.tool_calls],
                    }
                )
            elif isinstance(message, ToolMessage):
                history.append(
                    {
                        "role": "tool",
                        "tool": message.name,
                        "id": message.tool_call_id,
                        "status": message.status,
                        "content": message_text(message)[:4000],
                    }
                )
        return {
            "session_id": session_id,
            "messages": history,
            "summary": state.values.get("summary", ""),
            "allow_rules": state.values.get("allow_rules", []),
            "model": state.values.get("model") or settings.default_model_spec,
            "mode": state.values.get("mode", "build"),
            "todos": state.values.get("todos", []),
            "pending_approval": await pending_approval(runtime, session_id),
        }

    @app.post("/sessions/{session_id}/messages")
    async def send_message(session_id: str, body: MessageRequest, request: Request, _: None = Depends(authorize)):
        runtime = runtime_of(request)
        lock = await acquire(session_id)
        try:
            if await pending_approval(runtime, session_id):
                # A new message while an approval is pending counts as rejecting it, with the message as feedback.
                graph_input = Command(resume={"approved": False, "feedback": body.content})
            else:
                graph_input = {"messages": [HumanMessage(body.content)], "iterations": 0}
        except BaseException:
            lock.release()
            raise
        try:
            await runtime.sessions.touch(session_id, str(settings.workspace), body.content)
        except BaseException:
            lock.release()
            raise
        log.info("user message", extra={"session_id": session_id, "event": "message"})
        return sse(stream_events(runtime.graph, graph_input, config_for(session_id)), lock)

    @app.post("/sessions/{session_id}/approval")
    async def answer_approval(session_id: str, body: ApprovalRequest, request: Request, _: None = Depends(authorize)):
        runtime = runtime_of(request)
        lock = await acquire(session_id)
        try:
            if not await pending_approval(runtime, session_id):
                raise HTTPException(409, "no approval is pending for this session")
        except BaseException:
            lock.release()
            raise
        graph_input = Command(resume={"approved": body.approved, "feedback": body.feedback, "always": body.always})
        return sse(stream_events(runtime.graph, graph_input, config_for(session_id)), lock)

    @app.post("/sessions/{session_id}/compact")
    async def compact_session(session_id: str, request: Request, _: None = Depends(authorize)) -> dict:
        runtime = runtime_of(request)
        async with idle_session(runtime, session_id):
            config = config_for(session_id)
            state = await runtime.graph.aget_state(config)
            update = await runtime.nodes.compact(state.values, force=True)
            if not update:
                return {"removed": 0}
            removed = update.pop("removed")
            await runtime.graph.aupdate_state(config, update, as_node="agent")
            return {"removed": removed}

    @app.post("/sessions/{session_id}/undo")
    async def undo(session_id: str, request: Request, _: None = Depends(authorize)) -> dict:
        runtime = runtime_of(request)
        async with idle_session(runtime, session_id):
            result = FileHistory(settings.state_dir, session_id, Workspace(settings.workspace)).undo_last_turn()
            if result.changed:
                # Tell the model, so it doesn't assume its edits are still in place.
                note = f"[The user undid the agent's changes to: {', '.join(result.changed)}]"
                await runtime.graph.aupdate_state(config_for(session_id), {"messages": [HumanMessage(note)]}, as_node="agent")
            log.info("undo", extra={"session_id": session_id, "event": "undo"})
            return {"restored": result.restored, "deleted": result.deleted, "conflicts": result.conflicts}

    @app.get("/models")
    async def list_models(_: None = Depends(authorize)) -> dict:
        return {"default": settings.default_model_spec, "models": settings.available_models, "providers": configured_providers()}

    @app.post("/sessions/{session_id}/model")
    async def set_model(session_id: str, body: ModelRequest, request: Request, _: None = Depends(authorize)) -> dict:
        runtime = runtime_of(request)
        provider, name = parse_spec(body.model.strip(), settings.model_provider)
        spec = f"{provider}:{name}"
        if spec not in settings.available_models:
            raise HTTPException(400, f"{spec} is not configured; available: {', '.join(settings.available_models)} (set MODELS)")
        try:
            runtime.nodes.models_for(spec)  # fail now on a missing API key, not on the next message
        except ConfigurationError as exc:
            raise HTTPException(400, str(exc)) from exc
        async with idle_session(runtime, session_id):
            await runtime.graph.aupdate_state(config_for(session_id), {"model": spec}, as_node="agent")
        return {"model": spec}

    @app.post("/sessions/{session_id}/mode")
    async def set_mode(session_id: str, body: ModeRequest, request: Request, _: None = Depends(authorize)) -> dict:
        runtime = runtime_of(request)
        async with idle_session(runtime, session_id):
            await runtime.graph.aupdate_state(config_for(session_id), {"mode": body.mode}, as_node="agent")
        return {"mode": body.mode}

    @app.get("/memory")
    async def get_memory(_: None = Depends(authorize)) -> dict:
        source, _instructions = load_instructions(Workspace(settings.workspace))
        return {"instructions_source": source, "project": memory.read("project"), "global": memory.read("global")}

    @app.delete("/memory")
    async def clear_memory(scope: Literal["project", "global"], _: None = Depends(authorize)) -> dict:
        assert scope in SCOPES
        memory.clear(scope)
        return {"cleared": scope}

    return app
