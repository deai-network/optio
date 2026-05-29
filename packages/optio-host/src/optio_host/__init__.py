"""optio-host — local-or-remote host abstraction + log/deliverables protocol.

Top-level public API. See ``optio_host.host`` for primitives,
``optio_host.context`` for HookContext, and ``optio_host.protocol``
for the log/deliverables coordination protocol.
"""

from optio_host.context import (
    HookContext,
    HookContextProtocol,
)
from optio_host.download import DownloadFailed, create_download_task
from optio_host.host import (
    Host,
    HostCommandError,
    LocalHost,
    ProcessHandle,
    RemoteHost,
    RunResult,
    make_host,
)
from optio_host.types import SSHConfig

__all__ = [
    "Host",
    "LocalHost",
    "RemoteHost",
    "ProcessHandle",
    "make_host",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "SSHConfig",
    "DownloadFailed",
    "create_download_task",
]
