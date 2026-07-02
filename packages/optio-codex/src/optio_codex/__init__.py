"""optio-codex — run OpenAI Codex as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_codex.session import create_codex_task, run_codex_session
from optio_codex.types import (
    ApprovalPolicy,
    CodexTaskConfig,
    DeliverableCallback,
    HookCallback,
    IframeMode,
    SandboxMode,
)


_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_codex_task",
    "run_codex_session",
    "ApprovalPolicy",
    "CodexTaskConfig",
    "DeliverableCallback",
    "HookCallback",
    "IframeMode",
    "SandboxMode",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
]