"""Graph construction and the runtime that owns model, tools and checkpointer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from coding_agent.agent.llm import build_model
from coding_agent.agent.nodes import AgentNodes, ModelFactory, route_after_agent
from coding_agent.agent.state import AgentState
from coding_agent.config.settings import Settings
from coding_agent.services.approvals import ApprovalPolicy
from coding_agent.services.memory import MemoryStore
from coding_agent.services.sessions import SessionStore
from coding_agent.services.workspace import Workspace
from coding_agent.tools.filesystem import make_filesystem_tools
from coding_agent.tools.git import make_git_tools
from coding_agent.tools.mcp import load_mcp_tools
from coding_agent.tools.memory import make_memory_tools
from coding_agent.tools.shell import make_shell_tools
from coding_agent.tools.todos import make_todo_tools
from coding_agent.tools.web import make_web_tools


def build_tools(settings: Settings, workspace: Workspace) -> list[BaseTool]:
    return [
        *make_filesystem_tools(settings, workspace),
        *make_shell_tools(settings, workspace),
        *make_git_tools(settings, workspace),
        *make_memory_tools(MemoryStore(settings.state_dir, workspace.root)),
        *make_todo_tools(),
        *make_web_tools(settings),
    ]


def build_graph(
    settings: Settings,
    model: BaseChatModel,
    *,
    workspace: Workspace | None = None,
    extra_tools: list[BaseTool] | None = None,
    trusted_tools: set[str] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    model_factory: ModelFactory | None = None,
) -> CompiledStateGraph:
    graph, _nodes = build_agent(
        settings,
        model,
        workspace=workspace,
        extra_tools=extra_tools,
        trusted_tools=trusted_tools,
        checkpointer=checkpointer,
        model_factory=model_factory,
    )
    return graph


def build_agent(
    settings: Settings,
    model: BaseChatModel,
    *,
    workspace: Workspace | None = None,
    extra_tools: list[BaseTool] | None = None,
    trusted_tools: set[str] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    model_factory: ModelFactory | None = None,
) -> tuple[CompiledStateGraph, AgentNodes]:
    """Compile the graph and also return its nodes, which the API uses for out-of-run operations (compaction)."""
    workspace = workspace or Workspace(settings.workspace)
    tools = [*build_tools(settings, workspace), *(extra_tools or [])]
    policy = ApprovalPolicy(settings, workspace, trusted_tools)
    nodes = AgentNodes(settings, workspace, model, tools, policy, model_factory)

    graph = StateGraph(AgentState)
    graph.add_node("agent", nodes.agent)
    graph.add_node("approval", nodes.approval)
    graph.add_node("tools", nodes.tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["approval", END])
    graph.add_edge("approval", "tools")
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer), nodes


@dataclass
class Runtime:
    settings: Settings
    graph: CompiledStateGraph
    nodes: AgentNodes
    sessions: SessionStore
    mcp_errors: dict[str, str] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)


@asynccontextmanager
async def open_runtime(
    settings: Settings, model: BaseChatModel | None = None, model_factory: ModelFactory | None = None
) -> AsyncIterator[Runtime]:
    """Open the persistent checkpointer, load MCP tools and compile the graph."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    mcp = await load_mcp_tools(settings.mcp_config)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as checkpointer:
        graph, nodes = build_agent(
            settings,
            model or build_model(settings),
            extra_tools=mcp.tools,
            trusted_tools=mcp.trusted,
            checkpointer=checkpointer,
            model_factory=model_factory or (lambda spec: build_model(settings, spec)),
        )
        # The session index shares the checkpointer's SQLite connection and lock.
        sessions = SessionStore(checkpointer.conn, checkpointer.lock)
        await sessions.setup()
        yield Runtime(
            settings=settings,
            graph=graph,
            nodes=nodes,
            sessions=sessions,
            mcp_errors=mcp.errors,
            tool_names=sorted(nodes.tools_by_name),
        )
