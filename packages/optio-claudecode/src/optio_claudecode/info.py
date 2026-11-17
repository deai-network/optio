"""Canonical, user-facing metadata for the Claude Code engine (SSOT)."""

from optio_agents import AgentInfo

AGENT_INFO = AgentInfo(
    slug="claudecode",
    name="Claude Code",
    url="https://claude.com/product/claude-code",
)

# Claude Code through Requesty (ClaudeCodeRequestyTaskConfig).
REQUESTY_AGENT_INFO = AgentInfo(
    slug="claudecode_requesty",
    name="Claude Code + Requesty",
    url="https://requesty.ai",
)
