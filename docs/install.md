# Installing entroclaw

entroclaw has two parts, installed together:

- **`entroclaw`**: a single self-contained binary (the launcher and terminal UI).
- **`entroclaw-agent`**: the Python agent, installed as an isolated tool by [uv](https://docs.astral.sh/uv/). uv also provides Python 3.12, so you don't need Python installed.

When you run `entroclaw` in a directory, it starts `entroclaw-agent` for that directory on a random localhost port with a one-time access token. It stops the agent when you quit.

## Install

### Linux and macOS

```bash
curl -fsSL https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.sh | sh
```

The script:
1. Downloads the binary for your platform from GitHub Releases and **verifies its SHA-256 checksum**.
2. Installs it to `~/.entroclaw/bin`.
3. Installs uv if it's missing, then installs the matching agent version.
4. Adds `~/.entroclaw/bin` to PATH in your shell profile.

Options (pass after `sh -s --`):

```bash
curl -fsSL https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.sh | sh -s -- --version 0.2.0 --no-modify-path
```

| Option | |
|---|---|
| `--version X.Y.Z` | Install a specific release (default: latest) |
| `--no-modify-path` | Don't edit your shell profile |
| `--local` | Build and install from a source checkout (`./install.sh --local`) |
| `--uninstall` | Remove entroclaw (keeps settings and sessions) |

The `ENTROCLAW_INSTALL_DIR` environment variable changes where the binary goes.

**Alpine and other musl systems** need `apk add libstdc++ libgcc`.

### Windows

In PowerShell:

```powershell
irm https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.ps1 | iex
```

This installs to `%USERPROFILE%\.entroclaw\bin` and adds it to your user PATH; open a new terminal afterwards. Install [Git for Windows](https://git-scm.com/download/win) too: the agent uses its bash to run commands, falling back to PowerShell, and needs git for the git tools.

### npm

```bash
npm install -g entroclaw      # or: bun install -g entroclaw / pnpm add -g entroclaw
```

npm installs the binary for your platform as an optional dependency. The first time you run `entroclaw`, it offers to install the Python agent with uv (installing uv too if needed). Don't use `--omit=optional` / `--no-optional`, because that skips the binary.

## First run

```bash
entroclaw auth                 # choose a provider and paste an API key
cd your-project
entroclaw                      # or: entroclaw path/to/project
```

`entroclaw auth` stores keys in your config file with owner-only permissions (`0600`). Other settings:

```bash
entroclaw config                               # list settings (keys masked) and known options
entroclaw config set MODEL anthropic:claude-opus-5-5
entroclaw config set MODELS openai:gpt-4.1-mini,anthropic:claude-opus-5-5
entroclaw config unset SANDBOX_NETWORK
entroclaw config path
```

Environment variables override the config file.

## Where things live

| | Linux / macOS | Windows |
|---|---|---|
| Binary | `~/.entroclaw/bin/entroclaw` (npm: global `node_modules`) | `%USERPROFILE%\.entroclaw\bin\entroclaw.exe` |
| Agent | `~/.local/bin/entroclaw-agent` (uv tool) | `%USERPROFILE%\.local\bin\entroclaw-agent.exe` |
| Config | `~/.config/entroclaw/config.env` | `%APPDATA%\entroclaw\config.env` |
| Sessions, memory, undo history, logs | `~/.local/state/entroclaw/` | `%LOCALAPPDATA%\entroclaw\` |

`XDG_CONFIG_HOME` and `XDG_STATE_HOME` are respected. Nothing is written into your projects.

## Upgrade

```bash
entroclaw upgrade              # re-runs the installer for the latest release
npm install -g entroclaw@latest  # if you installed with npm
```

If the binary and the agent ever disagree on version, the UI shows a warning and `entroclaw doctor` reports it.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.sh | sh -s -- --uninstall
# Windows:  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/dev-sajid007/entroclaw/main/install.ps1))) -Uninstall
# npm:      npm uninstall -g entroclaw && uv tool uninstall entroclaw-agent
```

Settings and sessions are kept. To remove them too, delete the config and state directories listed above.

## Troubleshooting

Start with `entroclaw doctor`. It checks the agent, its version, uv, git, the shell, the sandbox, your config and API keys.

| Problem | Fix |
|---|---|
| `entroclaw: command not found` | Open a new terminal, or add `~/.entroclaw/bin` (and `~/.local/bin`) to PATH |
| "No API key is configured" | `entroclaw auth` |
| "The agent failed to start" | The message shows the end of the agent log; the full log is in the state directory (`server.log`) |
| Status bar says `unsandboxed` on Linux | Install bubblewrap (`apt install bubblewrap`). On Ubuntu 24.04+, unprivileged user namespaces must be allowed for bubblewrap; `entroclaw doctor` shows the exact reason |
| `npm i -g` succeeded but `entroclaw` says no prebuilt binary | Reinstall without `--omit=optional`, or use the install script |
| Corporate proxy | Set `HTTPS_PROXY`; the installer (curl), uv and the agent all honour it |
