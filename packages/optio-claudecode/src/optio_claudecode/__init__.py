"""optio-claudecode — run Anthropic Claude Code as an optio task."""

import logging as _logging

from optio_host import (
    HookContext,
    HookContextProtocol,
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_claudecode.session import create_claudecode_task, run_claudecode_session
from optio_claudecode.types import (
    ClaudeCodeTaskConfig,
    DeliverableCallback,
    HookCallback,
    PermissionMode,
)
from optio_claudecode.seed_manifest import (
    CLAUDE_SEED_MANIFEST,
    CLAUDE_SEED_SUFFIX,
    delete_seed,
    list_seeds,
)


# asyncssh emits per-connection INFO lines that flood worker stdout
# once an SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_claudecode_task",
    "run_claudecode_session",
    "ClaudeCodeTaskConfig",
    "DeliverableCallback",
    "HookCallback",
    "PermissionMode",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "CLAUDE_SEED_MANIFEST",
    "CLAUDE_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
]
