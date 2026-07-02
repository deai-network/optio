"""optio-codex — run OpenAI Codex as an optio task."""

import logging as _logging

from optio_agents import HookContext, HookContextProtocol
from optio_host import (
    HostCommandError,
    RunResult,
    SSHConfig,
)

from optio_codex.seed_manifest import (
    CODEX_CRED_MANIFEST,
    CODEX_SEED_MANIFEST,
    CODEX_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)
from optio_codex.session import create_codex_task, run_codex_session
from optio_codex.types import (
    ApprovalPolicy,
    CodexTaskConfig,
    ConversationMode,
    DeliverableCallback,
    HookCallback,
    SandboxMode,
    ToolVerbosity,
    SeedProvider,
    SeedUnavailableError,
)
from optio_codex.verify import verify_and_refresh_seed


_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_codex_task",
    "run_codex_session",
    "ApprovalPolicy",
    "CodexTaskConfig",
    "ConversationMode",
    "DeliverableCallback",
    "HookCallback",
    "SandboxMode",
    "ToolVerbosity",
    "SeedProvider",
    "SeedUnavailableError",
    "SSHConfig",
    "HookContext",
    "HookContextProtocol",
    "HostCommandError",
    "RunResult",
    "CODEX_SEED_MANIFEST",
    "CODEX_CRED_MANIFEST",
    "CODEX_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
    "purge_seed",
    "verify_and_refresh_seed",
]