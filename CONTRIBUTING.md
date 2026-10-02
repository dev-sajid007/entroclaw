# Contributing

## Setup

Requirements: Python 3.12 (uv installs it), [uv](https://docs.astral.sh/uv/), [Bun](https://bun.sh) ≥ 1.3, git, and optionally bubblewrap (`bwrap`) for the shell sandbox.

```bash
git clone https://github.com/dev-sajid007/entroclaw.git
cd entroclaw
(cd agent && uv sync && cp .env.example .env)   # add an API key to agent/.env to use a real model
(cd cli && bun install)
```

## Running

```bash
scripts/dev.sh /path/to/a/repo        # API + terminal UI against that repository
```

To try the UI without an API key, start the server with a scripted model:

```bash
echo '[{"content": "Hello from the scripted model."}]' > /tmp/script.json
cd agent && FAKE_MODEL_SCRIPT=/tmp/script.json uv run coding-agent serve
cd cli && bun run src/main.ts
```

## Tests and checks

Everything must pass before a change is merged (CI runs the same):

```bash
cd agent
uv run ruff check . && uv run ruff format --check .
uv run pytest

cd ../cli
bunx tsc --noEmit
bun test
```

- Tests never call a real model. Use `ScriptedChatModel(turns=[...])` or `ScriptedChatModel(responder=fn)` in Python, and `startAgentServer(script, files)` (`cli/test/support.ts`) for end-to-end tests.
- UI tests render real frames with OpenTUI's test renderer. Markdown parses asynchronously, so wait with `waitForText`.
- Sandbox tests skip automatically where bubblewrap isn't installed.
- Add a test for every bug fix and every new tool, policy rule, endpoint or event.

## Changing things

- **Tools:** add to `build_tools` in `agent/src/coding_agent/agent/graph.py` and classify them in `services/approvals.py`. Write tests for both the tool and its policy.
- **API or events:** update `docs/api.md`, the client in `cli/src/api/`, and the reducer in `cli/src/state/app-state.ts`.
- **Configuration:** add to `config/settings.py`, `agent/.env.example` and the table in `README.md`.
- **Benchmark tasks:** see [docs/evaluation.md](docs/evaluation.md).
- Record user-visible changes in [CHANGELOG.md](CHANGELOG.md).

See [AGENTS.md](AGENTS.md) for conventions and the safety invariants that must not be broken.

## Commits and pull requests

- Keep commits focused, with an imperative summary line ("Add web_search tool").
- Describe what changed, why, and how it was verified.
- Never commit secrets. `agent/.env` is ignored; the only committed `.env` files are the fake canaries in `agent/evals/tasks/`.

## Security issues

Please don't open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).
