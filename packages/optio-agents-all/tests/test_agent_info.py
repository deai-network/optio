import typing
from optio_agents_all import AGENTS, get_agent_info
from optio_agents_all.types import AgentType


def test_keys_match_agent_type():
    expected = set(typing.get_args(AgentType))
    assert set(AGENTS.keys()) == expected


def test_each_entry_slug_matches_key():
    for key, info in AGENTS.items():
        assert info.slug == key


def test_get_agent_info_lookup():
    assert get_agent_info("claudecode").name == "Claude Code"
    assert get_agent_info("grok").url == "https://x.ai/cli"
