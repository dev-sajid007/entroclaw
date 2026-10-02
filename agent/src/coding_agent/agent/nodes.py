"""Graph nodes: agent (LLM), approval (policy + human-in-the-loop), tools (execution)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.constants import TAG_NOSTREAM
from langgraph.graph import END
from langgraph.types import interrupt

from coding_agent.agent.context import PRODUCED_BY, estimate_tokens, fallback_summary, find_cut, prepare_messages, summarize
from coding_agent.agent.llm import provider_of
from coding_agent.agent.state import AgentState, ApprovalDecision
from coding_agent.config.settings import Settings
from coding_agent.prompts.coding_agent import PLAN_MODE_SECTION, build_system_prompt
from coding_agent.services.approvals import ApprovalPolicy, PolicyDecision, Risk
from coding_agent.services.history import FileHistory
from coding_agent.services.memory import MemoryStore, load_instructions
from coding_agent.services.workspace import Workspace
from coding_agent.tools.filesystem import FILE_WRITE_TOOL_NAMES, preview_file_change
from coding_agent.tools.todos import TodoError, validate_todos
from coding_agent.utils.logging import get_logger
from coding_agent.utils.security import truncate

log = get_logger("agent")
UI_RESULT_PREVIEW = 4000
PLAN_MODE_DENIAL = (
    "Plan mode: read-only. Investigate with read-only tools, then present your plan "
    "(update_todos + a short summary) and wait for the user to approve it."
)
ModelFactory = Callable[[str], BaseChatModel]


def last_ai_message(state: AgentState) -> AIMessage | None:
    messages = state.get("messages") or []
    if messages and isinstance(messages[-1], AIMessage):
        return messages[-1]
    return None


def repair_dangling_tool_calls(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Insert placeholder results for tool calls that never got one (e.g. an interrupted run),
    because providers reject histories with unanswered tool calls."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    repaired: list[AnyMessage] = []
    for message in messages:
        repaired.append(message)
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["id"] not in answered:
                    repaired.append(
                        ToolMessage(
                            content="Not executed: the run was interrupted.", tool_call_id=call["id"], name=call["name"], status="error"
                        )
                    )
    return repaired


def normalize_decision(answer: Any) -> ApprovalDecision:
    if isinstance(answer, bool):
        return {"approved": answer}
    if isinstance(answer, dict):
        return {
            "approved": bool(answer.get("approved")),
            "feedback": str(answer.get("feedback") or ""),
            "always": bool(answer.get("always")),
        }
    if isinstance(answer, str):
        return {"approved": answer.strip().lower() in {"y", "yes", "approve", "allow"}}
    return {"approved": False}


def session_of(config: RunnableConfig | None) -> str | None:
    return ((config or {}).get("configurable") or {}).get("thread_id")


def current_turn(messages: list[AnyMessage]) -> str:
    """Identify the user turn by the id of its HumanMessage (stable even after compaction)."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message.id or "unknown"
    return "unknown"


def safe_writer():
    try:
        return get_stream_writer()
    except Exception:
        return lambda _chunk: None


class AgentNodes:
    def __init__(
        self,
        settings: Settings,
        workspace: Workspace,
        model: BaseChatModel,
        tools: list[BaseTool],
        policy: ApprovalPolicy,
        model_factory: ModelFactory | None = None,
    ):
        self.settings = settings
        self.workspace = workspace
        self.tool_list = tools
        self.tools_by_name = {t.name: t for t in tools}
        self.policy = policy
        self.memory = MemoryStore(settings.state_dir, workspace.root)
        self.default_model = settings.default_model_spec
        self.model_factory = model_factory
        # spec -> (model bound to the tools, plain model for summaries hidden from the UI stream)
        self._models: dict[str, tuple[Any, Any]] = {}
        self._register(self.default_model, model)

    def _register(self, spec: str, model: BaseChatModel) -> tuple[Any, Any]:
        bound = model.bind_tools(self.tool_list) if self.tool_list else model
        self._models[spec] = (bound, model.with_config(tags=[TAG_NOSTREAM]))
        return self._models[spec]

    def models_for(self, spec: str | None) -> tuple[Any, Any]:
        """Bound and plain models for a session's model spec (built on first use)."""
        spec = spec or self.default_model
        if spec not in self._models:
            if self.model_factory is None:
                raise ValueError(f"model {spec!r} is not available")
            self._register(spec, self.model_factory(spec))
        return self._models[spec]

    def system_message(self, summary: str = "", provider: str = "", mode: str = "build") -> SystemMessage:
        """The stable prompt first, volatile parts (summary, mode) after it, so provider prompt caches stay warm."""
        stable = self.system_prompt()
        volatile = ""
        if summary:
            volatile += f"\n## Summary of the earlier conversation\n{summary}\n"
        if mode == "plan":
            volatile += PLAN_MODE_SECTION
        if provider == "anthropic":
            blocks = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}]
            if volatile:
                blocks.append({"type": "text", "text": volatile})
            return SystemMessage(content=blocks)
        return SystemMessage(stable + volatile)

    def system_prompt(self, summary: str = "") -> str:
        """Built on every call so edits to the instructions file and new memories apply immediately."""
        source, instructions = load_instructions(self.workspace)
        prompt = build_system_prompt(
            str(self.workspace.root),
            instructions_source=source,
            instructions=instructions,
            project_memory=self.memory.read("project"),
            global_memory=self.memory.read("global"),
        )
        if summary:
            prompt += f"\n## Summary of the earlier conversation\n{summary}\n"
        return prompt

    async def compact(self, state: AgentState, *, force: bool = False) -> dict | None:
        """Summarize and remove old messages when over the token budget (or always, if `force`).

        Returns a state update, or None when there is nothing to compact.
        """
        messages = state.get("messages") or []
        summary = state.get("summary", "")
        if not force and estimate_tokens(messages) + len(self.system_prompt(summary)) // 4 <= self.settings.context_token_limit:
            return None
        if force:
            # Manual compaction keeps only the latest user turn.
            cut = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
        else:
            cut = find_cut(messages, self.settings.context_keep_tokens)
        if cut <= 0:
            return None
        old = messages[:cut]
        fallback = False
        try:
            new_summary = await summarize(self.models_for(state.get("model"))[1], old, summary)
        except Exception:
            log.exception("summarization failed; dropping old messages instead")
            new_summary, fallback = fallback_summary(old, summary), True
        safe_writer()({"type": "context_compacted", "removed": len(old), "fallback": fallback})
        log.info("context compacted", extra={"event": "context_compacted", "agent_node": "agent"})
        return {"messages": [RemoveMessage(id=m.id) for m in old], "summary": new_summary, "removed": len(old)}

    async def agent(self, state: AgentState, config: RunnableConfig) -> dict:
        iterations = state.get("iterations", 0)
        if iterations >= self.settings.max_iterations:
            log.warning(
                "max iterations reached", extra={"agent_node": "agent", "event": "max_iterations", "session_id": session_of(config)}
            )
            return {
                "messages": [
                    AIMessage(
                        content=(
                            f"I stopped after {iterations} steps without finishing (the iteration limit). "
                            "Tell me how you'd like to proceed."
                        )
                    )
                ]
            }
        update: dict = {}
        messages = state["messages"]
        summary = state.get("summary", "")
        compaction = await self.compact(state)
        if compaction:
            removed = compaction.pop("removed")
            update = compaction
            messages, summary = messages[removed:], compaction["summary"]
        spec = state.get("model") or self.default_model
        provider = provider_of(spec)
        model_input = [
            self.system_message(summary, provider, state.get("mode", "build")),
            *prepare_messages(repair_dangling_tool_calls(messages), provider),
        ]
        response = await self.models_for(spec)[0].ainvoke(model_input, config)
        response = self.tag_response(response, spec)
        log.info(
            "model response",
            extra={
                "session_id": session_of(config),
                "agent_node": "agent",
                "model": spec,
                "token_usage": getattr(response, "usage_metadata", None),
                "event": "llm_call",
            },
        )
        return {**update, "messages": [*update.get("messages", []), response], "iterations": iterations + 1}

    @staticmethod
    def tag_response(response: AIMessage, spec: str) -> AIMessage:
        """Record which model produced the message, and make refusals visible instead of an empty answer."""
        metadata = {**(response.response_metadata or {}), PRODUCED_BY: spec}
        update: dict = {"response_metadata": metadata}
        if metadata.get("stop_reason") == "refusal" and not response.text and not response.tool_calls:
            details = metadata.get("stop_details") or {}
            category = details.get("category") if isinstance(details, dict) else None
            update["content"] = (
                f"The model declined this request{f' (category: {category})' if category else ''}. "
                "Try rephrasing, or switch models with /model."
            )
        return response.model_copy(update=update)

    def approval(self, state: AgentState, config: RunnableConfig) -> dict:
        """Evaluate each requested tool call; pause for the user on sensitive ones.

        This node has no side effects, so re-running it when the graph resumes after an interrupt is safe.
        """
        message = last_ai_message(state)
        decisions: dict[str, ApprovalDecision] = {}
        rules = list(state.get("allow_rules") or [])
        for call in message.tool_calls if message else []:
            name, args = call["name"], call["args"]
            decision = self.policy.evaluate(name, args)
            if state.get("mode") == "plan" and not decision.denied and self.policy.classify(name, args).risk is not Risk.SAFE:
                decision = PolicyDecision(Risk.DENIED, PLAN_MODE_DENIAL)
            if decision.denied:
                decisions[call["id"]] = {"approved": False, "feedback": f"Denied by policy: {decision.reason}", "by_policy": True}
                log.warning(
                    "tool call denied by policy",
                    extra={
                        "tool_name": name,
                        "tool_arguments": args,
                        "risk": decision.risk,
                        "event": "policy_denied",
                        "session_id": session_of(config),
                    },
                )
            elif decision.risk in (Risk.SENSITIVE, Risk.HIGH) and name in self.tools_by_name:
                rule = self.policy.rule_key(name, args)
                # High-risk actions always ask, even if the same kind of action was allowed before.
                allow_always = decision.risk is Risk.SENSITIVE
                if allow_always and rule in rules:
                    decisions[call["id"]] = {"approved": True}
                    continue
                request = {
                    "type": "approval_required",
                    "tool_call_id": call["id"],
                    "action": name,
                    "arguments": args,
                    "risk": str(decision.risk),
                    "reason": decision.reason,
                    "allow_always": allow_always,
                    "rule": rule,
                }
                if name in FILE_WRITE_TOOL_NAMES:
                    request["path"] = args.get("path")
                    request["diff"] = preview_file_change(self.settings, self.workspace, name, args)
                answer = normalize_decision(interrupt(request))
                if answer["approved"] and answer.get("always") and allow_always and rule not in rules:
                    rules.append(rule)
                decisions[call["id"]] = answer
                log.info(
                    "approval decision",
                    extra={
                        "session_id": session_of(config),
                        "tool_name": name,
                        "risk": decision.risk,
                        "event": "approval",
                        "tool_result_status": decisions[call["id"]]["approved"],
                    },
                )
            else:
                decisions[call["id"]] = {"approved": True}
        return {"decisions": decisions, "allow_rules": rules}

    async def tools(self, state: AgentState, config: RunnableConfig) -> dict:
        message = last_ai_message(state)
        decisions = state.get("decisions") or {}
        writer = safe_writer()
        results: list[ToolMessage] = []
        session_id = session_of(config)
        history = FileHistory(self.settings.state_dir, session_id, self.workspace) if session_id else None
        turn = current_turn(state.get("messages") or [])
        todos = None
        for call in message.tool_calls if message else []:
            name, args, call_id = call["name"], call["args"], call["id"]
            decision = decisions.get(call_id, {"approved": False, "feedback": "No approval decision was recorded."})
            if not decision.get("approved"):
                feedback = decision.get("feedback") or ""
                if decision.get("by_policy"):
                    content = f"{feedback} Choose a different, safer approach."
                    status = "denied"
                else:
                    content = "The user rejected this action. Do not retry it unchanged."
                    if feedback:
                        content += f" User feedback: {feedback}"
                    status = "rejected"
                writer({"type": "tool_end", "id": call_id, "tool": name, "status": status, "result": content})
                results.append(ToolMessage(content=content, tool_call_id=call_id, name=name, status="error"))
                continue

            writer({"type": "tool_start", "id": call_id, "tool": name, "args": args})
            started = time.monotonic()
            tool = self.tools_by_name.get(name)
            if name == "update_todos":
                # Updates graph state rather than the outside world, so it's handled here.
                try:
                    todos = validate_todos(args.get("todos"))
                    done = sum(t["status"] == "completed" for t in todos)
                    content, status = f"Todo list updated ({done}/{len(todos)} done).", "success"
                    writer({"type": "todos", "todos": todos})
                except TodoError as exc:
                    content, status = f"Error: {exc}", "error"
            elif tool is None:
                content, status = f"Error: unknown tool {name!r}. Available: {', '.join(sorted(self.tools_by_name))}", "error"
            else:
                snapshot = history.capture(str(args.get("path", ""))) if history and name in FILE_WRITE_TOOL_NAMES else None
                try:
                    output = await tool.ainvoke(args, config)
                    content, status = truncate(str(output), self.settings.max_tool_output), "success"
                    if snapshot is not None:
                        history.record(turn, snapshot)
                except Exception as exc:  # tool errors are reported back to the model, not raised
                    content, status = f"Error: {exc}", "error"
            duration = time.monotonic() - started
            log.info(
                "tool executed",
                extra={
                    "session_id": session_of(config),
                    "tool_name": name,
                    "tool_arguments": args,
                    "tool_duration": round(duration, 3),
                    "tool_result_status": status,
                    "event": "tool",
                },
            )
            writer(
                {
                    "type": "tool_end",
                    "id": call_id,
                    "tool": name,
                    "status": status,
                    "duration": round(duration, 3),
                    "result": truncate(content, UI_RESULT_PREVIEW),
                }
            )
            results.append(
                ToolMessage(content=content, tool_call_id=call_id, name=name, status="success" if status == "success" else "error")
            )
        update: dict = {"messages": results, "decisions": {}}
        if todos is not None:
            update["todos"] = todos
        return update


def route_after_agent(state: AgentState) -> str:
    message = last_ai_message(state)
    if message is not None and message.tool_calls:
        return "approval"
    return END
