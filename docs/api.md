# Agent API

The Python agent serves HTTP on `127.0.0.1:8765` by default (`HOST`, `PORT`). If `AGENT_API_TOKEN` is set, every request needs `Authorization: Bearer <token>`. Request and response bodies are JSON.

Endpoints that run the agent return a **`text/event-stream`**: one JSON event per `data:` line. A run ends with `final`, `approval_required` (paused, waiting for you) or `error`.

## Endpoints

| Method & path | Body | Returns |
|---|---|---|
| `GET /health` | — | `{status, version, shell, workspace, model, models, providers, require_approval, sandbox, tools, mcp_errors}` |
| `GET /models` | — | `{default, models, providers}` |
| `POST /sessions` | — | `{session_id}` |
| `GET /sessions?limit=20` | — | `{sessions: [{session_id, title, created_at, updated_at}]}` for this workspace, newest first |
| `GET /sessions/{id}` | — | `{session_id, messages, summary, allow_rules, model, mode, todos, pending_approval}` |
| `POST /sessions/{id}/messages` | `{content}` | **SSE.** Sends a user message. If an approval is pending, this rejects it with `content` as feedback. |
| `POST /sessions/{id}/approval` | `{approved, feedback?, always?}` | **SSE.** Answers the pending approval and continues. `always` adds a session rule (ignored for high risk). |
| `POST /sessions/{id}/compact` | — | `{removed}`: summarizes older messages now |
| `POST /sessions/{id}/undo` | — | `{restored, deleted, conflicts}`: reverts the last turn's file changes |
| `POST /sessions/{id}/model` | `{model}` | `{model}`: must be one of `/models`; 400 if its API key is missing |
| `POST /sessions/{id}/mode` | `{mode: "build" \| "plan"}` | `{mode}` |
| `GET /memory` | — | `{instructions_source, project, global}` |
| `DELETE /memory?scope=project\|global` | — | `{cleared}` |

**Status codes:**
- `409`: the session is already running, there's no pending approval to answer, or an approval is pending (compact, undo, model, mode).
- `400`: an invalid model.
- `401`: a missing or wrong bearer token.
- `422`: an invalid body.

`messages` in `GET /sessions/{id}` is a list of:
- `{role: "user", content}`
- `{role: "agent", content, tool_calls: [{id, tool, args}]}`
- `{role: "tool", id, tool, status, content}`

## Events

| `type` | Fields | Meaning |
|---|---|---|
| `run_start` | `session_id` | A run began |
| `agent_token` | `content` | Streamed reply text (append) |
| `agent_message` | `content` | The complete reply text for one model step |
| `tool_start` | `id, tool, args` | A tool started |
| `tool_output` | `tool, content` | Live command output (`run_command`) |
| `tool_end` | `id, tool, status, result, duration?` | `status`: `success` \| `error` \| `rejected` \| `denied` |
| `approval_required` | `tool_call_id, action, arguments, risk, reason, allow_always, rule, path?, diff?, interrupt_id` | Paused; answer with `/approval` |
| `todos` | `todos: [{content, status}]` | The checklist changed (`pending` \| `in_progress` \| `completed`) |
| `context_compacted` | `removed, fallback` | Older messages were summarized (`fallback`: dropped instead) |
| `usage` | `input_tokens, output_tokens, total_tokens` | Token usage for the run |
| `final` | `content` | The run finished; `content` is the last reply |
| `error` | `message` | The run failed |

## Example

```bash
SID=$(curl -s -X POST localhost:8765/sessions | jq -r .session_id)

curl -N -X POST localhost:8765/sessions/$SID/messages \
  -H 'content-type: application/json' -d '{"content": "Fix the failing test"}'
# data: {"type": "run_start", "session_id": "…"}
# data: {"type": "tool_start", "id": "call_1", "tool": "run_command", "args": {"command": "pytest -q"}}
# data: {"type": "tool_output", "tool": "run_command", "content": "F.\n"}
# …
# data: {"type": "approval_required", "action": "edit_file", "risk": "sensitive", "diff": "--- a/…", …}

curl -N -X POST localhost:8765/sessions/$SID/approval \
  -H 'content-type: application/json' -d '{"approved": true}'
# … data: {"type": "final", "content": "Fixed …"}
```
