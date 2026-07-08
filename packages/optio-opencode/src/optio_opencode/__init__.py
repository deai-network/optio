"""optio-opencode — run opencode web as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)
from optio_opencode.info import AGENT_INFO
from optio_opencode.session import create_opencode_task, run_opencode_session
from optio_opencode.types import (
    ConversationMode,
    DeliverableCallback,
    HookCallback,
    OpencodeTaskConfig,
    SeedProvider,
    ToolVerbosity,
)
from optio_opencode.seed_manifest import (
    OPENCODE_CRED_MANIFEST,
    OPENCODE_SEED_MANIFEST,
    OPENCODE_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)
from optio_opencode.verify import verify_and_refresh_seed

# asyncssh emits per-connection / per-channel INFO lines ("Opening SSH
# connection...", "Received channel close", etc.) that flood the worker's
# stdout once an SSH-backed opencode session starts.  Quiet it down by
# default.  Consumers that want the verbose trace can override:
#
#     logging.getLogger("asyncssh").setLevel(logging.INFO)
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)

__all__ = [
    "AGENT_INFO",
    "create_opencode_task",
    "run_opencode_session",
    "DeliverableCallback",
    "OpencodeTaskConfig",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "HookCallback",
    "OPENCODE_SEED_MANIFEST",
    "OPENCODE_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
    "purge_seed",
    "OPENCODE_CRED_MANIFEST",
    "SeedProvider",
    "ConversationMode",
    "ToolVerbosity",
    "verify_and_refresh_seed",
]
