"""optio-claudecode — run Anthropic Claude Code as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_claudecode.account import analyze_account
from optio_claudecode.info import AGENT_INFO
from optio_claudecode.session import create_claudecode_task, run_claudecode_session
from optio_claudecode.types import (
    AllowedDir,
    ClaudeCodeTaskConfig,
    DeliverableCallback,
    HookCallback,
    PermissionMode,
    ReasoningEffort,
)
from optio_claudecode.seed_manifest import (
    CLAUDE_SEED_MANIFEST,
    CLAUDE_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)


# asyncssh emits per-connection INFO lines that flood worker stdout
# once an SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "AGENT_INFO",
    "analyze_account",
    "create_claudecode_task",
    "run_claudecode_session",
    "AllowedDir",
    "ClaudeCodeTaskConfig",
    "DeliverableCallback",
    "HookCallback",
    "PermissionMode",
    "ReasoningEffort",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "CLAUDE_SEED_MANIFEST",
    "CLAUDE_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
    "purge_seed",
]
