"""optio-grok — run Grok Build (xAI) as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_grok.session import create_grok_task, run_grok_session
from optio_grok.types import (
    DeliverableCallback,
    GrokTaskConfig,
    HookCallback,
    PermissionMode,
)


# asyncssh emits per-connection INFO lines that flood worker stdout
# once an SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_grok_task",
    "run_grok_session",
    "GrokTaskConfig",
    "DeliverableCallback",
    "HookCallback",
    "PermissionMode",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
]
