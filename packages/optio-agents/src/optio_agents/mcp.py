"""MCP servers in the shared agent configuration.

A task can give its agent remote MCP servers (streamable HTTP only for now).
Every engine TaskConfig inherits ``McpConfigMixin``: ``mcp_servers`` is either
a static list of ``McpServer`` entries or a ``McpServersProvider`` awaited with
the process id (like ``SeedProvider``), so per-launch credentials stay fresh.
``resolve_mcp_servers`` is the one shared resolver; each engine calls it on
every launch, fresh and resume. An engine that cannot hand MCP servers to its
agent yet refuses any non-None value (``_validate_mcp(supported=False)``).

Header values carry credentials: they are never shown in a repr or an error
message."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class McpServer:
    """One remote MCP server: a name the agent knows it by (its tools appear
    as ``mcp__<name>__<tool>`` in Claude Code), an http(s) URL, and the HTTP
    headers sent with every request (e.g. ``Authorization``). ``repr`` shows
    the name and URL only."""
    name: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise ValueError(
                f"McpServer.name={self.name!r} must be non-empty and use only "
                "letters, digits, '_' and '-'."
            )
        parts = urlsplit(self.url) if isinstance(self.url, str) else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(
                f"McpServer {self.name!r}: url={self.url!r} must be an http(s) URL "
                "(only remote streamable-HTTP servers are supported)."
            )
        if not isinstance(self.headers, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.headers.items()
        ):
            raise ValueError(
                f"McpServer {self.name!r}: headers must map header names (str) "
                "to values (str)."
            )
        # Own a copy, so the caller's dict cannot change a frozen entry.
        object.__setattr__(self, "headers", dict(self.headers))


McpServersProvider = Callable[[str], Awaitable[list[McpServer]]]


def _check_servers(servers: Any, *, source: str) -> list[McpServer]:
    """Return ``servers`` as a list after checking it is a list of McpServer
    with unique names; raise ValueError naming ``source`` otherwise. Never
    puts an entry's repr in the message (it could carry header values)."""
    if not isinstance(servers, (list, tuple)):
        raise ValueError(
            f"{source} must be a list of McpServer, got {type(servers).__name__}."
        )
    seen: set[str] = set()
    for entry in servers:
        if not isinstance(entry, McpServer):
            raise ValueError(
                f"{source} must hold only McpServer entries, got {type(entry).__name__}."
            )
        if entry.name in seen:
            raise ValueError(f"{source}: duplicate MCP server name {entry.name!r}.")
        seen.add(entry.name)
    return list(servers)


@dataclass(frozen=True, kw_only=True)
class McpConfigMixin:
    """The ``mcp_servers`` setting, shared by every engine TaskConfig via
    inheritance. None (the default) gives the agent no MCP servers; a list is
    used as is on every launch; a provider is awaited with the process id on
    every launch."""
    mcp_servers: list[McpServer] | McpServersProvider | None = None

    def _validate_mcp(self, *, supported: bool, agent: str) -> None:
        """Raise if ``mcp_servers`` is set on an engine that does not support
        it yet, or if a static list is malformed. A provider's output is
        checked at launch by ``resolve_mcp_servers``. Call from each engine
        config's ``__post_init__``."""
        if self.mcp_servers is None:
            return
        if not supported:
            raise ValueError(f"{agent} does not support mcp_servers yet")
        if not callable(self.mcp_servers):
            _check_servers(self.mcp_servers, source=f"{type(self).__name__}.mcp_servers")


async def resolve_mcp_servers(config: Any, process_id: str) -> list[McpServer]:
    """The MCP servers for this launch of ``config``'s task: [] for None, the
    static list as is, or the provider's result (awaited with ``process_id``,
    then checked). A provider's exception propagates: it fails the launch."""
    servers = config.mcp_servers
    if servers is None:
        return []
    if callable(servers):
        return _check_servers(await servers(process_id),
                              source="the mcp_servers provider's result")
    return _check_servers(servers, source=f"{type(config).__name__}.mcp_servers")
