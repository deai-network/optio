"""MCP servers in the shared agent configuration: McpServer, the mixin's
validation and the one shared resolver."""
from dataclasses import dataclass

import pytest

from optio_agents import (McpConfigMixin, McpServer, McpServersProvider,
                          resolve_mcp_servers)


@dataclass(frozen=True, kw_only=True)
class _Cfg(McpConfigMixin):
    consumer_instructions: str = "x"


def _server(name="kontext", url="https://mcp.example/mcp", **kw):
    return McpServer(name=name, url=url, **kw)


# --- McpServer ---------------------------------------------------------------

def test_server_rejects_a_bad_name():
    with pytest.raises(ValueError, match="name"):
        _server(name="bad name")


def test_server_rejects_a_non_http_url():
    with pytest.raises(ValueError, match="url"):
        _server(url="ftp://x")


def test_server_repr_hides_headers():
    s = _server(headers={"Authorization": "Bearer secret"})
    assert "Bearer secret" not in repr(s)
    assert "kontext" in repr(s) and "https://mcp.example/mcp" in repr(s)
    assert s.headers == {"Authorization": "Bearer secret"}


# --- McpConfigMixin._validate_mcp ---------------------------------------------

def test_unsupported_agent_refuses_mcp_servers():
    cfg = _Cfg(mcp_servers=[_server()])
    with pytest.raises(ValueError, match="codex does not support mcp_servers yet"):
        cfg._validate_mcp(supported=False, agent="codex")


def test_unsupported_agent_accepts_none():
    cfg = _Cfg()
    assert cfg.mcp_servers is None
    cfg._validate_mcp(supported=False, agent="codex")


def test_static_list_with_duplicate_names_is_refused():
    cfg = _Cfg(mcp_servers=[_server(), _server(url="https://other.example/mcp")])
    with pytest.raises(ValueError, match="duplicate.*kontext"):
        cfg._validate_mcp(supported=True, agent="claudecode")


# --- resolve_mcp_servers --------------------------------------------------------

async def test_resolver_returns_a_static_list():
    servers = [_server(), _server(name="other", url="http://localhost:8080/mcp")]
    cfg = _Cfg(mcp_servers=servers)
    cfg._validate_mcp(supported=True, agent="claudecode")
    assert await resolve_mcp_servers(cfg, "pid-1") == servers


async def test_resolver_awaits_a_provider_with_the_process_id():
    seen = []

    async def provider(process_id: str) -> list[McpServer]:
        seen.append(process_id)
        return [_server()]

    typed: McpServersProvider = provider
    cfg = _Cfg(mcp_servers=typed)
    cfg._validate_mcp(supported=True, agent="claudecode")
    assert await resolve_mcp_servers(cfg, "pid-1") == [_server()]
    assert seen == ["pid-1"]


async def test_resolver_rejects_provider_output_that_is_not_mcp_servers():
    async def provider(process_id: str):
        return [{"name": "kontext", "url": "https://mcp.example/mcp",
                 "headers": {"Authorization": "Bearer secret"}}]

    cfg = _Cfg(mcp_servers=provider)
    with pytest.raises(ValueError, match="McpServer") as exc:
        await resolve_mcp_servers(cfg, "pid-1")
    assert "Bearer secret" not in str(exc.value)


async def test_resolver_returns_empty_for_none():
    assert await resolve_mcp_servers(_Cfg(), "pid-1") == []
