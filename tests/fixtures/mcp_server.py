"""Minimal stdio MCP server used by the MCP integration test."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("demo")


@server.tool()
def lookup_issue(number: int) -> str:
    """Look up an issue by number."""
    return f"Issue #{number}: login fails when the password contains a space"


@server.tool()
def close_issue(number: int) -> str:
    """Close an issue."""
    return f"Closed #{number}"


if __name__ == "__main__":
    server.run("stdio")
