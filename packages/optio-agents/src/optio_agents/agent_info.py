from dataclasses import dataclass


@dataclass(frozen=True)
class AgentInfo:
    """Canonical, user-facing metadata for an agent engine."""

    slug: str  # machine id, equals the engine's agent_type ("claudecode")
    name: str  # canonical user-facing name ("Claude Code")
    url: str   # canonical product URL
