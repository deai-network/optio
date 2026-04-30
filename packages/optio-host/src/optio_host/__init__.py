"""optio-host — local-or-remote host abstraction + log/deliverables protocol.

Top-level public API. See ``optio_host.host`` for primitives,
``optio_host.context`` for HookContext, and ``optio_host.protocol``
for the log/deliverables coordination protocol.
"""

from optio_host.context import (
    HookContext,
    HookContextProtocol,
    HostCommandError,
    RunResult,
)
from optio_host.host import Host, LaunchedProcess, LocalHost, RemoteHost, make_host
from optio_host.types import SSHConfig

__all__ = [
    "Host",
    "LocalHost",
    "RemoteHost",
    "LaunchedProcess",
    "make_host",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "SSHConfig",
]
