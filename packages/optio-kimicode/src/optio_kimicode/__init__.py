"""optio-kimicode — run Kimi Code CLI as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_kimicode.seed_manifest import (
    KIMI_CRED_MANIFEST,
    KIMI_SEED_MANIFEST,
    KIMI_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)
from optio_kimicode.session import create_kimicode_task, run_kimicode_session
from optio_kimicode.types import (
    AllowedDir,
    DeliverableCallback,
    HookCallback,
    KimiCodeTaskConfig,
    PermissionMode,
    SeedProvider,
    SeedUnavailableError,
)
from optio_kimicode.verify import verify_and_refresh_seed


# asyncssh emits per-connection INFO lines that flood worker stdout once an
# SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_kimicode_task",
    "run_kimicode_session",
    "KimiCodeTaskConfig",
    "DeliverableCallback",
    "HookCallback",
    "PermissionMode",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "SeedProvider",
    "SeedUnavailableError",
    "AllowedDir",
    "KIMI_SEED_MANIFEST",
    "KIMI_CRED_MANIFEST",
    "KIMI_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
    "purge_seed",
    "verify_and_refresh_seed",
]
