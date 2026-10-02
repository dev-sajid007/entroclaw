# entroclaw

An AI coding agent for your terminal. It reads, edits and tests code in your project, asks before it changes anything, and runs commands in a sandbox.

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.ps1 | iex

# or with npm (any OS)
npm install -g entroclaw
```

Then:

```bash
entroclaw auth                 # add an API key (Anthropic, OpenAI, …)
cd your-project && entroclaw   # start working
```

Other commands:

| Command | What it does |
|---|---|
| `entroclaw -c` | Continue the last session in this directory |
| `entroclaw run "fix the failing test" --yes` | Run one task headlessly |
| `entroclaw config` | Show and change settings |
| `entroclaw doctor` | Check the installation |
| `entroclaw upgrade` | Update to the latest release |

See [docs/install.md](docs/install.md) for details, uninstalling and troubleshooting.

Under the hood there are two parts:
- **`agent/`** (`entroclaw-agent`): Python 3.12 and LangGraph, with the tools, the approval policy and an HTTP + SSE API. Installed with uv.
- **`cli/`** (`entroclaw`): a TypeScript + OpenTUI terminal UI, compiled with Bun into a single binary that starts the agent for the current directory.

## Documentation

| | |
|---|---|
| [docs/install.md](docs/install.md) | Install methods, file locations, upgrade, uninstall, troubleshooting |
| [docs/architecture.md](docs/architecture.md) | How the graph, policy, state, persistence and UI fit together |
| [docs/api.md](docs/api.md) | HTTP endpoints and the SSE event protocol |
| [docs/evaluation.md](docs/evaluation.md) | Running and extending the benchmark |
| [SECURITY.md](SECURITY.md) | Threat model, defences, known limitations, reporting |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Running from source, tests, releasing |
| [AGENTS.md](AGENTS.md) | Repository guide for AI coding agents (also loaded by this agent) |
| [CHANGELOG.md](CHANGELOG.md) | What changed |
| [documentation.md](documentation.md) | The original design spec and milestones |

## In the UI

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
| `/model [n\|name]` | list models or switch this session's model |
| `/plan [request]`, `/go` | plan mode (read-only investigation + todo list), then carry out the plan |
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

### Models

Models are named `provider:model`. `MODEL` is the default, and `MODELS` lists extra models offered by `/model`, which switches per session. OpenAI, Anthropic and any provider supported by LangChain's `init_chat_model` work; set each provider's API key.

Claude models (e.g. `anthropic:claude-opus-5-5`) are configured for agentic coding:
- adaptive thinking, with depth set by `MODEL_EFFORT` (default `high`)
- streaming, with 64k max output tokens
- no sampling parameters
- the system prompt is cached
- thinking blocks from earlier turns are stripped, and edited history is sent with `drop_block`, so compaction and model switches never fail a request
- server-side refusal fallback (`fallbacks: "default"`) is on; set `ANTHROPIC_FALLBACKS=off` to disable it

### Plan mode and todos

`/plan` limits the agent to read-only tools: reading, searching, allowlisted commands and its todo list. Anything else is denied without asking. The agent investigates, writes the steps with `update_todos` (shown in a panel above the input) and proposes a plan. `/go` switches back to build mode and carries it out. The todo list is also used in normal mode for multi-step work.

### Context, instructions and memory

- **Context management.** When the conversation exceeds `CONTEXT_TOKEN_LIMIT`, older turns are summarized by the model and removed; roughly `CONTEXT_KEEP_TOKENS` of recent history stays verbatim. Cuts never separate a tool call from its result. If summarization fails, older turns are dropped and the user's requests are kept.
- **Project instructions.** The first of `AGENTS.md`, `CLAUDE.md` or `.entroclaw/instructions.md` at the workspace root is added to the system prompt on every call. These files can't override the safety rules.
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

**Sandbox.** When bubblewrap is installed (`SANDBOX=auto`, the default), every `run_command` runs inside it:
- the filesystem is read-only except the workspace, a private `/tmp` and `~/.cache`
- there's no network (`SANDBOX_NETWORK=true` allows it)
- `~/.ssh`, `~/.aws`, `~/.gnupg`, cloud/CLI credential directories, `.netrc`-style files, the agent's state directory and its `.env` are hidden

The sandbox contains approved commands; approval rules still decide what runs. Git tools run outside it with fixed arguments. Without bubblewrap the status bar shows `unsandboxed`, and you can use the Docker image instead.

**Web.** `fetch_url` (always available) and `web_search` (with `TAVILY_API_KEY` or `BRAVE_SEARCH_API_KEY`) need approval, because a URL or query can carry data out. Always-allow works per domain. Private, loopback, link-local and cloud-metadata addresses are refused, and checked again on every redirect. Fetched content is labelled untrusted.

## Configuration

Settings live in your config file (`entroclaw config path`: `~/.config/entroclaw/config.env`, or `%APPDATA%\entroclaw\config.env` on Windows). Change them with `entroclaw config set KEY VALUE`; environment variables override the file. Running from source uses `agent/.env` instead (see [agent/.env.example](agent/.env.example)).

| Variable | Default | |
|---|---|---|
| `OPENAI_API_KEY` | — | required for the real model |
| `MODEL` / `MODEL_PROVIDER` / `OPENAI_BASE_URL` | `gpt-4.1-mini` / `openai` / — | any LangChain `init_chat_model` provider or OpenAI-compatible endpoint |
| `WORKSPACE` | current directory | the only directory the agent can touch |
| `MAX_TOOL_OUTPUT`, `COMMAND_TIMEOUT`, `MAX_ITERATIONS` | 20000, 30, 25 | |
| `REQUIRE_APPROVAL` | `true` | |
| `CONTEXT_TOKEN_LIMIT`, `CONTEXT_KEEP_TOKENS` | 100000, 30000 | when to summarize, and how much recent history to keep |
| `STATE_DIR` | `~/.local/state/entroclaw` (`%LOCALAPPDATA%\entroclaw` on Windows) | checkpoints, sessions, memory, undo snapshots, server log |
| `MODELS`, `MODEL_EFFORT`, `ANTHROPIC_FALLBACKS` | —, `high`, `default` | extra models for `/model`; Claude effort; Claude refusal fallback |
| `SANDBOX`, `SANDBOX_NETWORK` | `auto`, `false` | bubblewrap sandbox for shell commands |
| `TAVILY_API_KEY` / `BRAVE_SEARCH_API_KEY`, `WEB_ALLOW_PRIVATE` | —, `false` | web search provider; allow private-network fetches |
| `CHECKPOINT_PATH` | `$STATE_DIR/checkpoints.sqlite` | |
| `MCP_CONFIG` | — | path to an MCP config, see [agent/mcp.example.json](agent/mcp.example.json) |
| `AGENT_API_TOKEN` | — | require `Authorization: Bearer …` |
| `HOST`, `PORT`, `LOG_LEVEL` | `127.0.0.1`, `8765`, `INFO` | |

## Testing and evaluation

```bash
cd agent && uv run pytest          # tools, policy, security, graph, context, memory, undo, API, MCP, evals, headless e2e
cd cli && bun test                 # reducer, SSE parser, history, rendered UI frames, full-stack e2e (starts the Python agent)
cd agent && uv run entroclaw-agent eval [--only 001-add-missing-function] [--baseline old-report.json]
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
docker build -t entroclaw-agent ./agent
docker run --rm -p 127.0.0.1:8765:8765 -e OPENAI_API_KEY -v "$PWD:/workspace" -v entroclaw-state:/state entroclaw-agent
```

The container runs as an unprivileged user and keeps its checkpoints in the `/state` volume. CI (`.github/workflows/ci.yml`) runs lint, both test suites and the Docker build. When an `OPENAI_API_KEY` secret is configured, it also runs the benchmark.
