from optio_agents import AgentInfo

from optio_claudecode import AGENT_INFO, REQUESTY_AGENT_INFO
from optio_claudecode.types import ClaudeCodeRequestyTaskConfig, ClaudeCodeTaskConfig


def test_agent_info_values():
    assert AGENT_INFO.slug == "claudecode"
    assert AGENT_INFO.name == "Claude Code"
    assert AGENT_INFO.url == "https://claude.com/product/claude-code"


def test_agent_info_slug_matches_agent_type():
    # agent_type Literal default on the config discriminant must equal the slug
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(ClaudeCodeTaskConfig)}
    assert fields["agent_type"].default == AGENT_INFO.slug


def test_requesty_agent_info_values():
    assert REQUESTY_AGENT_INFO == AgentInfo(
        slug="claudecode_requesty",
        name="Claude Code + Requesty",
        url="https://requesty.ai",
    )


def test_requesty_agent_info_slug_matches_agent_type():
    assert (
        REQUESTY_AGENT_INFO.slug
        == ClaudeCodeRequestyTaskConfig.__dataclass_fields__["agent_type"].default
    )
