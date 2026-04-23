"""optio-opencode — run opencode web as an optio task."""

import logging as _logging

from optio_opencode.session import create_opencode_task, run_opencode_session
from optio_opencode.types import (
    DeliverableCallback,
    OpencodeTaskConfig,
    SSHConfig,
)

# asyncssh emits per-connection / per-channel INFO lines ("Opening SSH
# connection...", "Received channel close", etc.) that flood the worker's
# stdout once an SSH-backed opencode session starts.  Quiet it down by
# default.  Consumers that want the verbose trace can override:
#
#     logging.getLogger("asyncssh").setLevel(logging.INFO)
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)

__all__ = [
    "create_opencode_task",
    "run_opencode_session",
    "DeliverableCallback",
    "OpencodeTaskConfig",
    "SSHConfig",
]
