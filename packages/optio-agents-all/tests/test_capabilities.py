"""Which engines support which optional capabilities, and that each engine's
config agrees: an engine outside ``MCP_AGENT_TYPES`` refuses ``mcp_servers``
at construction, an engine inside it accepts them."""
from typing import get_args

import pytest

from optio_agents import McpServer

import optio_agents_all as aa
from optio_agents_all import MCP_AGENT_TYPES, ClaudeCodeTaskConfig, supports_mcp

CONFIG_TYPES = get_args(aa.AgentTaskConfig)
UNSUPPORTED_CONFIG_TYPES = [c for c in CONFIG_TYPES if c is not ClaudeCodeTaskConfig]

_SERVER = McpServer(name="k", url="https://k.example/mcp")


def _make(cls, **kw):
    """Minimal valid config of any engine (claustrum opted out); the same
    kwargs as test_config_uniformity."""
    return cls(consumer_instructions="x", fs_isolation=False, **kw)


async def _provider(process_id: str) -> list[McpServer]:
    return [_SERVER]


def test_only_claudecode_supports_mcp():
    assert isinstance(MCP_AGENT_TYPES, frozenset)
    assert MCP_AGENT_TYPES == {"claudecode"}
    assert supports_mcp("claudecode") is True
    assert supports_mcp("codex") is False


@pytest.mark.parametrize("cls", UNSUPPORTED_CONFIG_TYPES, ids=lambda c: c.__name__)
def test_unsupported_configs_refuse_mcp_servers(cls):
    agent = cls.__dataclass_fields__["agent_type"].default
    assert not supports_mcp(agent)
    with pytest.raises(ValueError, match=f"{agent} does not support mcp_servers yet"):
        _make(cls, mcp_servers=[_SERVER])
    with pytest.raises(ValueError, match=f"{agent} does not support mcp_servers yet"):
        _make(cls, mcp_servers=_provider)
    # Unset stays valid on every engine.
    assert _make(cls).mcp_servers is None


def test_claudecode_config_accepts_mcp_servers():
    assert _make(ClaudeCodeTaskConfig, mcp_servers=[_SERVER]).mcp_servers == [_SERVER]
    assert _make(ClaudeCodeTaskConfig, mcp_servers=_provider).mcp_servers is _provider
    # A malformed static list is still refused on the supporting engine.
    with pytest.raises(ValueError, match="duplicate MCP server name"):
        _make(ClaudeCodeTaskConfig, mcp_servers=[_SERVER, _SERVER])
