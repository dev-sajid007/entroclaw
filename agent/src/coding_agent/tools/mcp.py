"""MCP integration: load tools from MCP servers declared in a JSON config file.

Config format (see mcp.example.json):

    {
      "servers": {
        "<name>": {
          "transport": "stdio", "command": "npx", "args": ["-y", "..."],
          "env": {"TOKEN": "${GITHUB_TOKEN}"},
          "trusted_tools": ["read_only_tool"]
        },
        "<name2>": {"transport": "streamable_http", "url": "http://localhost:8000/mcp"}
      }
    }

Tools are exposed as `<server>_<tool>`. Every MCP tool requires approval unless listed in `trusted_tools`.
`${VAR}` references are expanded from the environment so secrets stay out of the file.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import BaseTool

from coding_agent.utils.logging import get_logger

log = get_logger("mcp")
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class MCPTools:
    tools: list[BaseTool] = field(default_factory=list)
    trusted: set[str] = field(default_factory=set)
    errors: dict[str, str] = field(default_factory=dict)


def _expand(value):
    if isinstance(value, str):
        return ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def parse_config(path: Path) -> tuple[dict[str, dict], dict[str, set[str]]]:
    raw = json.loads(path.read_text())
    servers = raw.get("servers", raw.get("mcpServers", {}))
    connections: dict[str, dict] = {}
    trusted: dict[str, set[str]] = {}
    for name, spec in servers.items():
        spec = dict(spec)
        trusted[name] = set(spec.pop("trusted_tools", []))
        spec = _expand(spec)
        if "transport" not in spec:
            spec["transport"] = "stdio" if "command" in spec else "streamable_http"
        connections[name] = spec
    return connections, trusted


async def load_mcp_tools(config_path: Path | None) -> MCPTools:
    result = MCPTools()
    if config_path is None:
        return result
    if not config_path.exists():
        log.warning("MCP config not found", extra={"error": str(config_path)})
        return result
    from langchain_mcp_adapters.client import MultiServerMCPClient

    connections, trusted = parse_config(config_path)
    client = MultiServerMCPClient(connections, tool_name_prefix=True)
    for server in connections:
        try:
            tools = await client.get_tools(server_name=server)
        except Exception as exc:  # one broken server must not take the agent down
            result.errors[server] = str(exc)
            log.error("MCP server failed to load", extra={"error": f"{server}: {exc}"})
            continue
        result.tools.extend(tools)
        result.trusted.update(f"{server}_{name}" for name in trusted.get(server, set()))
        log.info("MCP server loaded", extra={"event": "mcp_loaded", "tool_name": [t.name for t in tools]})
    return result
