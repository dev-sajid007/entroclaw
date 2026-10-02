# Coding Agent

A terminal AI coding assistant with two deliberately separate parts:

- **`agent/`**: Python 3.12, LangGraph runtime, tools, policy/approval layer, HTTP + SSE API
- **`cli/`**: TypeScript, Bun and OpenTUI terminal UI

The full design is in [coding-agent-documentation.md](coding-agent-documentation.md).

## Quick start

```bash
# 1. configure
cp agent/.env.example agent/.env      # then set OPENAI_API_KEY (and MODEL)
(cd agent && uv sync) && (cd cli && bun install)

# 2. run against a repository (starts the API, then opens the UI)
scripts/dev.sh /path/to/your/repo
```

Or run the two processes yourself:

```bash
cd agent && WORKSPACE=/path/to/repo uv run coding-agent serve
cd cli && bun run src/main.ts                  # -c resumes the latest session, --session <id> a specific one
```

Headless, without the UI:

```bash
cd agent && WORKSPACE=/path/to/repo uv run coding-agent run "Fix the failing test"   # --yes auto-approves
```

### In the UI

| Input | Effect |
|---|---|
| text + Enter | send a message (Ctrl+J or Shift/Alt+Enter for a new line) |
| ↑ / ↓ | previous / next prompt (history is kept across runs) |
| `y` / `n` | allow or reject the pending action |
| `a` | allow, and stop asking for this kind of action for the rest of the session (not offered for high-risk actions) |
| any other text while an approval is pending | reject it and pass the text to the agent as feedback |
| Esc | cancel the running task |
| PgUp / PgDn | scroll |
| `/sessions`, `/resume <n\|id>` | list this workspace's sessions and resume one |
| `/undo` | revert the files the agent changed in its last turn |
| `/compact` | summarize older messages now |
| `/memory`, `/forget [project\|global]` | show or clear remembered notes |
| `/rules` | show what is always allowed in this session |
| `/new`, `/session`, `/clear`, `/help`, `/quit` | other commands |

Agent replies render as markdown. Fenced code is syntax-highlighted for JS/TS; other languages show as plain code.

## Architecture

```
OpenTUI (cli/) ──HTTP/SSE──► FastAPI (server/api.py) ──► LangGraph
                                                         START → agent ─┬─ tool calls ─► approval ─► tools ─┐
                                                                        └─ answer ─► END   ▲                │
                                                                                           └── agent ◄──────┘
```

- **agent** calls the LLM with the system prompt and tool schemas. It stops after `MAX_ITERATIONS` model calls per user turn.
- **approval** has no side effects. It runs every requested tool call through the policy (`services/approvals.py`) and pauses the graph (`interrupt`) for sensitive ones. File writes carry a unified diff to the UI. Because the node does nothing else, re-running it on resume can't execute anything twice.
- **tools** executes the approved calls and streams `tool_start` / `tool_output` / `tool_end` events. Failures go back to the model as tool errors, so it can recover.
- State is checkpointed to SQLite (`CHECKPOINT_PATH`). Sessions, including a pending approval and "always allow" rules, survive a server restart and can be resumed (`-c`, `--session`, `/resume`).

### Context, instructions and memory

- **Context management.** When the conversation exceeds `CONTEXT_TOKEN_LIMIT`, older turns are summarized by the model and removed; roughly `CONTEXT_KEEP_TOKENS` of recent history stays verbatim. Cuts never separate a tool call from its result. If summarization fails, older turns are dropped and the user's requests are kept.
- **Project instructions.** The first of `AGENTS.md`, `CLAUDE.md` or `.coding-agent/instructions.md` at the workspace root is added to the system prompt on every call. These files can't override the safety rules.
- **Memory.** The agent's `remember` tool saves short, durable notes (preferences, conventions, decisions) to the state directory, never to the repository. Notes are per project or global and are included in later sessions. Secret-looking notes are refused.
- **Undo.** Before each `write_file` / `edit_file`, the previous content is snapshotted in the state directory. `/undo` restores the last turn's files and deletes files the agent created. A file changed by someone else since is skipped and reported. Changes made through `run_command` are not tracked.

### Tools

| Tool | Policy |
|---|---|
| `list_files`, `read_file`, `search_files` | read-only |
| `write_file`, `edit_file` | approval with diff. Secrets files are high risk; `.git/` and paths outside the workspace are denied |
| `run_command` | read-only allowlist runs freely (tests, linters, `git status`…); other commands need approval; destructive ones (`rm -rf`, `git reset/clean/push`, `kill`) are high risk; catastrophic ones (`sudo`, `rm -rf /`, `curl \| sh`, `mkfs`…) are denied |
| `git_status`, `git_diff`, `git_log`, `git_show` | read-only |
| `git_branch` (create), `git_add`, `git_commit` | approval |
| MCP tools (`<server>_<tool>`) | approval unless listed in `trusted_tools` |

`REQUIRE_APPROVAL=false` turns off prompts for ordinary sensitive actions. High-risk actions still ask, and denied ones are still denied.

### Security model

- All paths resolve inside the workspace; symlink escapes are blocked.
- `.env`, keys, `credentials*` and similar files can't be read or searched.
- Child processes get an environment with credential-like variables removed.
- Commands run without stdin, with a timeout that kills the whole process group, and with output limits.
- Logs are structured JSON with token and key patterns redacted.
- The system prompt tells the model that tool output is untrusted data, not instructions.
- The API binds to 127.0.0.1. Set `AGENT_API_TOKEN` to require a bearer token.
- Only `agent/.env` (or `AGENT_ENV_FILE`) is loaded, never a `.env` in the workspace.

`run_command` is not an OS sandbox: an approved command runs with your user's permissions. For stronger isolation, run the agent in the Docker image.

## Configuration

All settings are environment variables. See [agent/.env.example](agent/.env.example).

| Variable | Default | |
|---|---|---|
| `OPENAI_API_KEY` | — | required for the real model |
| `MODEL` / `MODEL_PROVIDER` / `OPENAI_BASE_URL` | `gpt-4.1-mini` / `openai` / — | any LangChain `init_chat_model` provider or OpenAI-compatible endpoint |
| `WORKSPACE` | current directory | the only directory the agent can touch |
| `MAX_TOOL_OUTPUT`, `COMMAND_TIMEOUT`, `MAX_ITERATIONS` | 20000, 30, 25 | |
| `REQUIRE_APPROVAL` | `true` | |
| `CONTEXT_TOKEN_LIMIT`, `CONTEXT_KEEP_TOKENS` | 100000, 30000 | when to summarize, and how much recent history to keep |
| `STATE_DIR` | `~/.local/state/coding-agent` | checkpoints, sessions, memory and undo snapshots |
| `CHECKPOINT_PATH` | `$STATE_DIR/checkpoints.sqlite` | |
| `MCP_CONFIG` | — | path to an MCP config, see [agent/mcp.example.json](agent/mcp.example.json) |
| `AGENT_API_TOKEN` | — | require `Authorization: Bearer …` |
| `HOST`, `PORT`, `LOG_LEVEL` | `127.0.0.1`, `8765`, `INFO` | |

## Testing and evaluation

```bash
cd agent && uv run pytest          # tools, policy, security, graph, context, memory, undo, API, MCP, evals, headless e2e
cd cli && bun test                 # reducer, SSE parser, history, rendered UI frames, full-stack e2e (starts the Python agent)
cd agent && uv run coding-agent eval [--only 001-add-missing-function] [--baseline old-report.json]
```

Tests use a scripted model (`agent/fake.py`), so they need no API key. The benchmark (`agent/evals/tasks/`) runs the real model on fixture repositories:

- add a missing function
- fix a failing test
- refactor a module
- find a bug without modifying files
- implement a feature
- resist a prompt injection that tries to leak a canary secret from `.env`
- refuse a destructive command

Each report records success, tool selection, tool errors and recovery, iterations, tokens, latency, check results, policy denials and safety violations. With `--baseline`, it also lists regressions against an earlier report.

## Docker

```bash
docker build -t coding-agent ./agent
docker run --rm -p 127.0.0.1:8765:8765 -e OPENAI_API_KEY -v "$PWD:/workspace" -v coding-agent-state:/state coding-agent
```

The container runs as an unprivileged user and keeps its checkpoints in the `/state` volume. CI (`.github/workflows/ci.yml`) runs lint, both test suites and the Docker build. When an `OPENAI_API_KEY` secret is configured, it also runs the benchmark.
