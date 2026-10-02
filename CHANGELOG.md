# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Installable as `entroclaw`:** a single binary (launcher + TUI) that starts the agent for the current directory on a random localhost port with a one-time token, and stops it on exit.
  - Install with `install.sh` (Linux/macOS), `install.ps1` (Windows) or `npm install -g entroclaw`. Installers verify SHA-256 checksums.
  - The Python agent is installed with uv; the npm install offers to install it on first run.
  - Subcommands: `auth`, `config`, `run`, `serve`, `attach`, `doctor`, `upgrade`, `--version`.
- **Release workflow:** builds binaries for linux-x64, linux-x64-musl, linux-arm64, darwin-x64, darwin-arm64 and windows-x64, smoke-tests each against the agent wheel, publishes a GitHub Release with `SHA256SUMS`, and publishes npm packages.
- **Windows support in the agent:** commands run in Git Bash (falling back to PowerShell), and process trees are stopped with `taskkill`.
- `/health` reports the agent `version` and `shell`.
- **Model providers:** models are named `provider:model` (OpenAI, Anthropic, and any provider LangChain's `init_chat_model` supports). `MODELS` lists models for per-session switching with `/model` and the `POST /sessions/{id}/model` endpoint. Claude models use adaptive thinking with `MODEL_EFFORT`, 64k streaming output, a cached system prompt, tolerant thinking-block binding and server-side refusal fallback (`ANTHROPIC_FALLBACKS`).
- **Shell sandbox:** `run_command` runs inside bubblewrap when available (`SANDBOX=auto|bwrap|off`). The filesystem is read-only except the workspace, `/tmp` and `~/.cache`; there's no network unless `SANDBOX_NETWORK=true`; credential locations are hidden.
- **Plan mode:** `/plan` limits the agent to read-only actions until `/go`.
- **Todo list:** the `update_todos` tool, shown live in a panel above the input.
- **Web tools:** `fetch_url` (with an SSRF guard) and `web_search` (Tavily or Brave), both behind approval.
- Docs: `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/architecture.md`, `docs/api.md`, `docs/evaluation.md`.

### Changed
- The Python package is now `entroclaw-agent` (command `entroclaw-agent`; `coding-agent` remains an alias).
- Config is read from `~/.config/entroclaw/config.env` (`%APPDATA%\entroclaw` on Windows) when installed. State moved to `~/.local/state/entroclaw` (`%LOCALAPPDATA%\entroclaw`).
- With `SANDBOX=auto`, the sandbox is only used when bubblewrap can actually create one (e.g. not on Ubuntu 24.04 with unprivileged user namespaces blocked); otherwise commands run unsandboxed and the reason is reported.
- `scripts/dev.sh` runs the launcher from source, which starts the agent itself.
- A headless `run` only logs warnings unless `LOG_LEVEL` is set.
- `/health` reports models, configured providers and sandbox status.
- The Docker image sets `SANDBOX=off`, since the container is the isolation boundary.

## [0.1.0] - initial commit

### Added
- **Context management:** older turns are summarized when the conversation exceeds `CONTEXT_TOKEN_LIMIT`, never splitting a tool call from its result. `/compact` triggers it manually.
- **Project instructions** from `AGENTS.md`, `CLAUDE.md` or `.entroclaw/instructions.md`.
- **Memory:** the `remember` tool, with notes stored per project or globally outside the repository. `/memory` shows them and `/forget` clears them.
- **Undo:** `/undo` reverts the last turn's file changes and reports files changed since.
- **Always allow:** answer `a` to an approval to skip repeat prompts for that action this session (never for high-risk actions). `/rules` lists them.
- **Sessions:** `/sessions` lists this workspace's sessions, `/resume <n|id>` loads one, and `--continue` resumes the latest.
- **UI:** markdown rendering with syntax-highlighted code, multi-line input (Ctrl+J), and persistent prompt history (↑/↓).
- **LangGraph agent loop** (agent → approval → tools) with SQLite checkpointing; sessions survive restarts.
- **Tools:** `list_files`, `read_file`, `search_files`, `write_file`, `edit_file`, `run_command`, and git status/diff/log/show/branch/add/commit.
- **Approval policy** with diffs, a read-only command allowlist, high-risk detection, a denylist and workspace confinement.
- **HTTP + SSE API** streaming tokens, tool activity, command output and approval requests.
- **OpenTUI terminal UI** with chat, tool calls, diff approvals, status bar and cancellation.
- **MCP client** (`MCP_CONFIG`) with approval for untrusted tools.
- **Evaluation benchmark** (`entroclaw-agent eval`) with five task types plus prompt-injection and destructive-command tasks, and regression comparison.
- Headless `entroclaw-agent run`, a Dockerfile, CI and structured JSON logging.
