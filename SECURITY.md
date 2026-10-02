# Security

Coding Agent lets a language model read files, edit code and run commands on your machine. This document describes how that power is contained, what is *not* protected, and how to report problems.

## Threat model

The model is treated as **capable but not trusted**. It can be wrong, and it can be steered by text it reads: prompt injection in a file, a web page, a tool result or an MCP server response. The defences below assume any tool call may be adversarial.

## Defences

| Layer | What it does | Where |
|---|---|---|
| Workspace confinement | Every path resolves inside `WORKSPACE`, including through symlinks. `.git/` can't be written. | `utils/security.py`, `services/workspace.py` |
| Secret files | `.env*` (except examples), keys, `credentials*`, `.netrc` and similar can't be read or searched; writing them is high risk. | `utils/security.py` |
| Approval policy | Every tool call is classified as safe, sensitive (asks you), high risk (always asks, even with "always allow" or `REQUIRE_APPROVAL=false`) or denied (never runs: `sudo`, `rm -rf /`, `curl … \| sh`, `mkfs`, …). | `services/approvals.py` |
| Diffs before writes | File changes are shown as a unified diff before approval. | `tools/filesystem.py` |
| Plan mode | Only safe (read-only) actions run; everything else is denied without asking. | `agent/nodes.py` |
| Shell sandbox | With bubblewrap: read-only filesystem except the workspace, private `/tmp` and `~/.cache`; no network; credential directories, the agent's state and its `.env` hidden. | `services/sandbox.py` |
| Environment filtering | Child processes don't get variables that look like credentials (`*KEY*`, `*TOKEN*`, `*SECRET*`, …). | `utils/security.py` |
| Limits | Command timeouts kill the whole process group; tool output and file reads are size-capped; runs stop after `MAX_ITERATIONS`. | `tools/shell.py`, settings |
| Web | `fetch_url` and `web_search` need approval (URLs and queries can exfiltrate data). Private, loopback, link-local and metadata addresses are refused on every redirect. Content is labelled untrusted. | `tools/web.py` |
| Memory | Notes are stored outside the repository, and notes that look like secrets are refused. | `services/memory.py` |
| Untrusted content | The system prompt treats files, command output and web content as data, never instructions. Project instructions can't override the safety rules. | `prompts/coding_agent.py` |
| API | Binds to `127.0.0.1`; set `AGENT_API_TOKEN` to require a bearer token. | `server/api.py` |
| Logs | Structured JSON with key and token patterns redacted. | `utils/logging.py` |
| Agent config | Only `agent/.env` (or `AGENT_ENV_FILE`) is loaded, never a `.env` in the workspace. | `config/settings.py` |

## Known limitations

- **Approval is the main control.** An approved command can still do anything the sandbox allows: write anywhere in the workspace, or read files outside the hidden locations. Read what you approve, especially high-risk actions.
- **Without bubblewrap there is no sandbox.** Commands run with your user's permissions; the status bar shows `unsandboxed`. Use the Docker image for isolation on such systems.
- **Secret hiding is a denylist.** The sandbox hides well-known credential locations, but the rest of the filesystem stays readable. Keep secrets in standard places, or run in Docker.
- **The git tools run outside the sandbox.** They use fixed arguments with no shell, and their write operations need approval.
- **MCP servers run with your permissions.** They aren't sandboxed. Their tools need approval unless you list them in `trusted_tools`; only trust servers you would run yourself.
- **Prompt injection can't be fully prevented.** The controls above limit what an injected instruction can achieve, but a model may still be misled into proposing harmful changes. Review diffs.
- **Undo** covers `write_file` / `edit_file` only, not changes made through shell commands.

## Running safely

- Point `WORKSPACE` at the repository you're working on, not your home directory.
- Keep `REQUIRE_APPROVAL=true` and the sandbox on (`SANDBOX=auto` or `bwrap`).
- Only set `SANDBOX_NETWORK=true` or `WEB_ALLOW_PRIVATE=true` when you need them.
- If the API is reachable by anyone other than you, set `AGENT_API_TOKEN`.

## Reporting a vulnerability

Please email **dev.sajid007@gmail.com** with the details and steps to reproduce, instead of opening a public issue. You'll get a reply within a week, and fixes will be credited unless you prefer otherwise.
