"""optio-cursor — run Cursor CLI (cursor-agent) as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_cursor.info import AGENT_INFO
from optio_cursor.seed_manifest import (
    CURSOR_CRED_MANIFEST,
    CURSOR_SEED_MANIFEST,
    CURSOR_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)
from optio_cursor.session import create_cursor_task, run_cursor_session
from optio_cursor.types import (
    CursorTaskConfig,
    DeliverableCallback,
    HookCallback,
    SeedProvider,
    SeedUnavailableError,
)
from optio_cursor.verify import verify_and_refresh_seed


# asyncssh emits per-connection INFO lines that flood worker stdout
# once an SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "AGENT_INFO",
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
    "CURSOR_SEED_MANIFEST",
    "CURSOR_CRED_MANIFEST",
    "CURSOR_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
    "purge_seed",
    "SeedProvider",
    "SeedUnavailableError",
    "verify_and_refresh_seed",
]
