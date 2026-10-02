"""Runtime configuration loaded from environment variables / `.env`."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

TRUE_VALUES = {"1", "true", "yes", "on"}
AGENT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "coding-agent"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in TRUE_VALUES


@dataclass(frozen=True)
class Settings:
    model: str = "gpt-4.1-mini"
    model_provider: str = "openai"
    base_url: str | None = None
    # Extra models offered for per-session switching, as "provider:model" specs.
    models: tuple[str, ...] = ()
    model_effort: str = "high"
    anthropic_fallbacks: str = "default"
    # Shell sandbox: "auto" (bubblewrap when available), "bwrap" (required), or "off".
    sandbox: str = "auto"
    sandbox_network: bool = False
    # Web tools.
    web_allow_private: bool = False
    tavily_api_key: str | None = field(default=None, repr=False)
    brave_api_key: str | None = field(default=None, repr=False)
    workspace: Path = field(default_factory=Path.cwd)
    max_tool_output: int = 20_000
    max_file_bytes: int = 1_000_000
    command_timeout: int = 30
    require_approval: bool = True
    max_iterations: int = 25
    context_token_limit: int = 100_000
    context_keep_tokens: int = 30_000
    # Agent-owned state (checkpoints, memory, undo snapshots, sessions); never inside the workspace.
    state_dir: Path = field(default_factory=_state_dir)
    checkpoint_path: Path = field(default_factory=lambda: _state_dir() / "checkpoints.sqlite")
    mcp_config: Path | None = None
    fake_model_script: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_env(cls, env_file: str | Path | None = None, **overrides: object) -> Settings:
        # Only the agent's own .env is loaded, never one from the current directory: running the agent
        # inside a project must not pull that project's secrets into the agent process.
        load_dotenv(env_file or os.environ.get("AGENT_ENV_FILE") or AGENT_ENV_FILE, override=False)
        workspace = Path(os.environ.get("WORKSPACE") or Path.cwd()).expanduser().resolve()
        mcp_config = os.environ.get("MCP_CONFIG")
        fake_script = os.environ.get("FAKE_MODEL_SCRIPT")
        checkpoint = os.environ.get("CHECKPOINT_PATH")
        state_env = os.environ.get("STATE_DIR")
        if state_env:
            state_dir = Path(state_env).expanduser()
        elif checkpoint:
            state_dir = Path(checkpoint).expanduser().parent
        else:
            state_dir = _state_dir()
        settings = cls(
            model=os.environ.get("MODEL") or cls.model,
            model_provider=os.environ.get("MODEL_PROVIDER") or cls.model_provider,
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            models=tuple(m.strip() for m in os.environ.get("MODELS", "").split(",") if m.strip()),
            model_effort=os.environ.get("MODEL_EFFORT") or cls.model_effort,
            anthropic_fallbacks=os.environ.get("ANTHROPIC_FALLBACKS") or cls.anthropic_fallbacks,
            sandbox=(os.environ.get("SANDBOX") or cls.sandbox).lower(),
            sandbox_network=_env_bool("SANDBOX_NETWORK", cls.sandbox_network),
            web_allow_private=_env_bool("WEB_ALLOW_PRIVATE", cls.web_allow_private),
            tavily_api_key=os.environ.get("TAVILY_API_KEY") or None,
            brave_api_key=os.environ.get("BRAVE_SEARCH_API_KEY") or None,
            workspace=workspace,
            max_tool_output=_env_int("MAX_TOOL_OUTPUT", cls.max_tool_output),
            max_file_bytes=_env_int("MAX_FILE_BYTES", cls.max_file_bytes),
            command_timeout=_env_int("COMMAND_TIMEOUT", cls.command_timeout),
            require_approval=_env_bool("REQUIRE_APPROVAL", cls.require_approval),
            max_iterations=_env_int("MAX_ITERATIONS", cls.max_iterations),
            context_token_limit=_env_int("CONTEXT_TOKEN_LIMIT", cls.context_token_limit),
            context_keep_tokens=_env_int("CONTEXT_KEEP_TOKENS", cls.context_keep_tokens),
            state_dir=state_dir,
            checkpoint_path=Path(checkpoint).expanduser() if checkpoint else state_dir / "checkpoints.sqlite",
            mcp_config=Path(mcp_config).expanduser() if mcp_config else None,
            fake_model_script=Path(fake_script).expanduser() if fake_script else None,
            host=os.environ.get("HOST") or cls.host,
            port=_env_int("PORT", cls.port),
        )
        return settings.with_overrides(**overrides) if overrides else settings

    @property
    def default_model_spec(self) -> str:
        """The default model as "provider:model"."""
        from coding_agent.agent.llm import parse_spec

        provider, model = parse_spec(self.model, self.model_provider)
        return f"{provider}:{model}"

    @property
    def available_models(self) -> list[str]:
        from coding_agent.agent.llm import parse_spec

        specs = [self.default_model_spec]
        for spec in self.models:
            provider, model = parse_spec(spec, self.model_provider)
            if f"{provider}:{model}" not in specs:
                specs.append(f"{provider}:{model}")
        return specs

    def with_overrides(self, **overrides: object) -> Settings:
        values = {k: v for k, v in overrides.items() if v is not None}
        if "workspace" in values:
            values["workspace"] = Path(values["workspace"]).expanduser().resolve()
        return replace(self, **values)
