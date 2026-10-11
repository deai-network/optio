"""Which engines support which optional capabilities, and that each engine's
config agrees: an engine outside ``MCP_AGENT_TYPES`` refuses ``mcp_servers``
at construction, an engine inside it accepts them; likewise for
``custom_controls`` and ``CUSTOM_CONTROLS_AGENT_TYPES``."""
from typing import get_args

import pytest

from optio_agents import ControlSettings, CustomControl, CustomOption, McpServer
from optio_agents.custom_controls import KNOWN_PERMISSION_MODES, KNOWN_REASONING_EFFORTS
from optio_claudecode.controls import PERMISSION_MODES as CLAUDECODE_PERMISSION_MODES
from optio_claudecode.models import EFFORT_LEVELS as CLAUDECODE_EFFORT_LEVELS

import optio_agents_all as aa
from optio_agents_all import (CUSTOM_CONTROLS_AGENT_TYPES, MCP_AGENT_TYPES,
                              ClaudeCodeTaskConfig, supports_custom_controls, supports_mcp)

CONFIG_TYPES = get_args(aa.AgentTaskConfig)
UNSUPPORTED_CONFIG_TYPES = [c for c in CONFIG_TYPES if c is not ClaudeCodeTaskConfig]

_SERVER = McpServer(name="k", url="https://k.example/mcp")

_SPEED = CustomControl(
    id="speed", label={"en": "Model", "hu": "Modell", "de": "Modell"}, default="extensive",
    options=[
        CustomOption(value="fast", label="Fast",
                     settings=ControlSettings(model="sonnet", reasoning_effort="medium")),
        CustomOption(value="extensive", label="Extensive",
                     settings=ControlSettings(model="opus", reasoning_effort="xhigh")),
    ],
)


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


# --- custom session controls ---------------------------------------------------------

def test_only_claudecode_supports_custom_controls():
    assert isinstance(CUSTOM_CONTROLS_AGENT_TYPES, frozenset)
    assert CUSTOM_CONTROLS_AGENT_TYPES == {"claudecode"}
    assert supports_custom_controls("claudecode") is True
    assert supports_custom_controls("codex") is False


@pytest.mark.parametrize("cls", UNSUPPORTED_CONFIG_TYPES, ids=lambda c: c.__name__)
def test_unsupported_configs_refuse_custom_controls(cls):
    agent = cls.__dataclass_fields__["agent_type"].default
    assert not supports_custom_controls(agent)
    with pytest.raises(ValueError, match=f"{agent} does not support custom_controls yet"):
        _make(cls, custom_controls=[_SPEED])
    # Unset or empty stays valid on every engine.
    assert _make(cls).custom_controls is None
    assert _make(cls, custom_controls=[]).custom_controls == []


def test_claudecode_config_accepts_custom_controls():
    assert _make(ClaudeCodeTaskConfig, custom_controls=[_SPEED]).custom_controls == [_SPEED]
    # A malformed list is still refused on the supporting engine.
    with pytest.raises(ValueError, match="duplicate control id 'speed'"):
        _make(ClaudeCodeTaskConfig, custom_controls=[_SPEED, _SPEED])


def test_known_values_match_the_supporting_engine():
    # optio-agents cannot import an engine, so it keeps its own copy of the
    # known effort levels and permission modes: they must stay Claude Code's.
    assert list(KNOWN_REASONING_EFFORTS) == list(CLAUDECODE_EFFORT_LEVELS)
    assert list(KNOWN_PERMISSION_MODES) == list(CLAUDECODE_PERMISSION_MODES)
