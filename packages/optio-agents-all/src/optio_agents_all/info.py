from __future__ import annotations

from optio_agents import AgentInfo
from optio_antigravity import AGENT_INFO as _antigravity_info
from optio_claudecode import AGENT_INFO as _claudecode_info
from optio_codex import AGENT_INFO as _codex_info
from optio_cursor import AGENT_INFO as _cursor_info
from optio_grok import AGENT_INFO as _grok_info
from optio_kimicode import AGENT_INFO as _kimicode_info
from optio_opencode import AGENT_INFO as _opencode_info

from .types import AgentType

AGENTS: dict[AgentType, AgentInfo] = {
    "claudecode": _claudecode_info,
    "opencode": _opencode_info,
    "codex": _codex_info,
    "cursor": _cursor_info,
    "grok": _grok_info,
    "kimicode": _kimicode_info,
    "antigravity": _antigravity_info,
}


def get_agent_info(agent_type: AgentType) -> AgentInfo:
    """Return canonical metadata for an agent engine."""
    return AGENTS[agent_type]
