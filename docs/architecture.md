# Architecture

Coding Agent has two processes that talk over HTTP and Server-Sent Events:

```
┌──────────────── cli/ (Bun + OpenTUI) ────────────────┐        ┌──────────────── agent/ (Python) ─────────────────┐
│ App ── AppStore(reduce) ── views                      │  HTTP  │ FastAPI (server/api.py)                          │
│  header · chat (markdown) · todos · input · status    │ ─────► │   └─ stream_events (server/events.py)            │
│ AgentClient ── parseSSE                               │ ◄───── │        └─ LangGraph graph + SQLite checkpointer  │
└───────────────────────────────────────────────────────┘  SSE   └──────────────────────────────────────────────────┘
```

The UI only renders state and collects input; the Python runtime owns execution and is the source of truth. Any client that speaks the [API](api.md) can drive it: the headless `coding-agent run` command and the eval runner use the same graph directly.

## The graph

```
START → agent ─┬─ tool calls ─► approval ─► tools ─┐
               │                                    │
               └─ answer ─► END        agent ◄──────┘
```

`agent/graph.py` builds it from three `AgentNodes` methods (`agent/nodes.py`):

**`agent`**
1. Stops with a message once `MAX_ITERATIONS` model calls have run this turn.
2. Compacts the context if it's over budget.
3. Builds the system message: the base prompt, project instructions, memory, then the volatile summary and plan-mode section.
4. Prepares the history for the session's provider (`prepare_messages`) and calls the session's model.
5. Tags the reply with the model that produced it.

**`approval`** evaluates each requested call with `ApprovalPolicy`:
- denied → recorded as denied
- plan mode and not safe → denied
- sensitive or high risk → `interrupt()` for the user, unless a session "always allow" rule covers a sensitive call
- safe → approved

It has **no side effects**, because LangGraph re-runs the node from the start when a paused run resumes. That's what makes several approvals in one step safe.

**`tools`**
- Runs approved calls in order and returns rejections and denials to the model as error results.
- Handles `update_todos` itself, since it updates state.
- Snapshots files before `write_file` / `edit_file` for undo.
- Streams `tool_start` / `tool_output` / `tool_end` events through LangGraph's custom stream writer.

### State (`agent/state.py`)

| Field | Purpose |
|---|---|
| `messages` | Conversation (LangChain messages, `add_messages` reducer) |
| `decisions` | Approval outcome per tool call for the current batch |
| `iterations` | Model calls this user turn |
| `summary` | Summary of messages removed by compaction |
| `allow_rules` | Session "always allow" rules (`edit_file`, `run_command:<cmd>`, `fetch_url:<host>`, …) |
| `model` | Per-session model override (`provider:model`) |
| `mode` | `build` or `plan` |
| `todos` | The agent's checklist |

State is checkpointed after every step to SQLite (`AsyncSqliteSaver`, `CHECKPOINT_PATH`), keyed by session id (`thread_id`). A paused approval survives a server restart. Changes made outside a run (model, mode, compaction, the undo note) use `graph.aupdate_state(..., as_node="agent")` while the session is idle.

## Policy and safety

`services/approvals.py` classifies every call:

| Risk | Examples | Behaviour |
|---|---|---|
| safe | reads, searches, `git status`, allowlisted commands (`pytest`, `ruff check`, …), `remember`, `update_todos` | runs |
| sensitive | file writes, other commands, git writes, MCP tools, web | asks (or an "always allow" rule) |
| high | `rm -rf dir`, `git reset/clean/push`, writing secrets files | always asks |
| denied | `sudo`, `rm -rf /`, `curl … \| sh`, writes outside the workspace or into `.git/` | never runs |

Commands then run in the bubblewrap sandbox (`services/sandbox.py`) with a filtered environment. See [SECURITY.md](../SECURITY.md).

## Context management (`agent/context.py`)

When the estimated token count exceeds `CONTEXT_TOKEN_LIMIT`:
- `find_cut` picks the earliest user-turn boundary that keeps about `CONTEXT_KEEP_TOKENS` of recent history. It falls back to an agent-step boundary, never between a tool call and its result.
- The older messages are summarized by the model, with that call hidden from the UI stream (`nostream` tag), and removed with `RemoveMessage`.
- If summarization fails, a fallback keeps the user's requests verbatim.

`prepare_messages` removes reasoning/thinking blocks from turns before the current one. After a model switch, it reduces other providers' messages to text plus tool calls.

## Models (`agent/llm.py`)

`build_model(settings, spec)` maps `provider:model` to `init_chat_model`. `AgentNodes` caches one tool-bound model and one plain model per spec.

Claude models get extra request settings:
- adaptive thinking with `output_config.effort`
- 64k max tokens with streaming
- no sampling parameters
- `drop_block` thinking binding, because compaction and model switches edit history
- the server-side refusal fallback
- a cache breakpoint on the stable part of the system prompt

## Persistence

Everything lives in `STATE_DIR` (default `~/.local/state/coding-agent`), never in the workspace:

| Path | Contents |
|---|---|
| `checkpoints.sqlite` | LangGraph checkpoints + the `sessions` index table (`services/sessions.py`) |
| `memory/global.md`, `memory/<hash>.md` | Remembered notes (`services/memory.py`) |
| `snapshots/<session>/changes.jsonl` | Undo history (`services/history.py`) |

The CLI keeps its prompt history in `~/.local/state/coding-agent/cli-history.json`.

## The terminal UI (`cli/`)

- `AgentClient` (`src/api/agent.ts`) wraps the REST endpoints and parses SSE into typed `AgentEvent`s.
- `AppStore` applies events with the pure `reduce` (`src/state/app-state.ts`). Updates are immutable, so `ChatView.sync` re-renders only items whose identity changed. Streaming agent replies update a single `MarkdownRenderable` in place.
- `App` (`src/app.ts`) wires the views, slash commands, approvals (`y` / `a` / `n` / feedback), cancellation (Esc aborts the fetch, which cancels the server run) and resuming.

## Extension points

- **Tools:** add a factory to `build_tools` and classify it in `ApprovalPolicy._classify`.
- **MCP servers:** `MCP_CONFIG` (see `agent/mcp.example.json`); tools are named `<server>_<tool>`.
- **Providers:** any `init_chat_model` provider; add its key to `PROVIDER_KEYS` for a clear error message.
- **Benchmark tasks:** [evaluation.md](evaluation.md).
