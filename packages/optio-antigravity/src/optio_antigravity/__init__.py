"""optio-antigravity — run Google Antigravity (agy) as an optio task."""

import logging as _logging

from optio_antigravity.session import (
    create_antigravity_task,
    run_antigravity_session,
)
from optio_antigravity.types import AntigravityTaskConfig


# asyncssh emits per-connection INFO lines that flood worker stdout
# once an SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_antigravity_task",
    "run_antigravity_session",
    "AntigravityTaskConfig",
]
