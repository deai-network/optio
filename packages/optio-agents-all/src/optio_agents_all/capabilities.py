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


# Engines whose TaskConfig accepts a non-empty ``custom_controls`` (and applies
# the picks to its agent). Every other engine's config raises ValueError on a
# non-empty list.
CUSTOM_CONTROLS_AGENT_TYPES: frozenset[str] = frozenset({"claudecode"})


def supports_custom_controls(agent_type: str) -> bool:
    """Whether the ``agent_type`` engine can show and apply custom session
    controls (``custom_controls=`` on its TaskConfig)."""
    return agent_type in CUSTOM_CONTROLS_AGENT_TYPES
