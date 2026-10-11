"""Optional capabilities only some engines support, so a caller can ask before
building a config (each engine's TaskConfig refuses an unsupported setting at
construction)."""

from __future__ import annotations

# Engines whose TaskConfig accepts ``mcp_servers`` (and hands the servers to its
# agent). Every other engine's config raises ValueError on any non-None value.
MCP_AGENT_TYPES: frozenset[str] = frozenset({"claudecode"})


def supports_mcp(agent_type: str) -> bool:
    """Whether the ``agent_type`` engine can give its agent MCP servers
    (``mcp_servers=`` on its TaskConfig)."""
    return agent_type in MCP_AGENT_TYPES
