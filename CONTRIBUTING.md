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

From source, the launcher starts the agent with `uv run`, so nothing needs installing. To try the UI without an API key, use a scripted model:

```bash
echo '[{"content": "Hello from the scripted model."}]' > /tmp/script.json
FAKE_MODEL_SCRIPT=/tmp/script.json scripts/dev.sh /path/to/a/repo
```

To test the installed experience from your checkout, run `./install.sh --local`. It builds the binary, installs the agent with uv, and puts `entroclaw` on your PATH.

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

## Releasing

1. Bump the version in **all three** of `cli/package.json`, `agent/pyproject.toml` and `npm/entroclaw/package.json`, then run `uv lock` in `agent/`.
2. Move the `[Unreleased]` entries in `CHANGELOG.md` under the new version.
3. Commit, then tag and push: `git tag v0.3.0 && git push origin main v0.3.0`.

The **Release** workflow checks that the versions match the tag, then:
- builds the agent wheel
- compiles `entroclaw` for linux-x64 / linux-x64-musl / linux-arm64 / darwin-x64 / darwin-arm64 / windows-x64 on native runners
- smoke-tests each binary with the wheel and a scripted model
- publishes a GitHub Release with `SHA256SUMS`
- publishes the npm packages when the `NPM_TOKEN` repository secret is set

`install.sh` and `install.ps1` always download from the latest release.

## Security issues

Please don't open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).
