"""optio-cursor — run Cursor CLI (cursor-agent) as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)

# Task 5 (session.py) re-enables:
# from optio_cursor.session import create_cursor_task, run_cursor_session
from optio_cursor.types import (
    CursorTaskConfig,
    DeliverableCallback,
    HookCallback,
)


# asyncssh emits per-connection INFO lines that flood worker stdout
# once an SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_cursor_task",
    "run_cursor_session",
    "CursorTaskConfig",
    "DeliverableCallback",
    "HookCallback",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
]
